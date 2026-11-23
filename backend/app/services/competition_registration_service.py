from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.competition_account import (
    CompetitionAccessGrant,
    CompetitionEntry,
    Team,
    TeamJoinRequest,
    TeamMembership,
)
from app.models.notification import Notification
from app.models.user import User
from app.services.lock_service import hold_advisory_xact_lock


class CompetitionRegistrationService:
    """Confirm official entries and keep membership entitlements in sync."""

    def __init__(self, db: Session):
        self.db = db

    def status(self, user: User, competition_id: str, initial_budget_cny: float) -> dict:
        competition_id = competition_id.strip().lower()
        from app.services.classroom_service import ClassroomService
        classes = ClassroomService(self.db)
        if competition_id != "first-competition" and classes.membership(user.id):
            classes.require_track(user.id, competition_id)
            info = classes.my_class(user.id)
            team = info["team"]
            return self._status_payload(competition_id, Decimal(0), registered=team is not None,
                can_confirm=False, team=self.db.get(Team, team["id"]) if team else None,
                is_team_leader=info["is_team_leader"], remaining_budget=Decimal(0),
                message="Your class team is assigned by course staff." if team else "Your class team is awaiting assignment by course staff.")
        initial_budget = Decimal("0.00")
        if competition_id == "first-competition":
            entry = self.db.scalar(select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.user_id == user.id,
                CompetitionEntry.confirmed_at.is_not(None),
            ))
            return self._status_payload(
                competition_id, entry.initial_budget_cny if entry else initial_budget,
                registered=entry is not None, can_confirm=entry is None,
                remaining_budget=None,
                message="Your personal entry is confirmed." if entry else "Confirm to enter this competition.",
            )
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership is None:
            return self._status_payload(
                competition_id,
                initial_budget,
                registered=False,
                can_confirm=True,
                message="Confirm your team to enter this official competition.",
            )

        team = self.db.get(Team, membership.team_id)
        entry = self._confirmed_entry(competition_id, membership.team_id)
        if team is not None and entry is not None:
            return self._status_payload(
                competition_id,
                entry.initial_budget_cny,
                registered=True,
                can_confirm=False,
                team=team,
                is_team_leader=team.leader_user_id == user.id,
                remaining_budget=None,
                message="Your team is confirmed for this competition.",
            )

        # A pre-existing, unconfirmed team is replaced by the official
        # registration flow.  Keep the confirmation page available so the
        # user is not permanently blocked by stale local membership data.
        return self._status_payload(
            competition_id,
            initial_budget,
            registered=False,
            can_confirm=True,
            team=team,
            is_team_leader=bool(team and team.leader_user_id == user.id),
            message="Confirming will close your current unregistered team and create the official competition team.",
        )

    def confirm(
        self,
        user: User,
        competition_id: str,
        member_emails: list[str],
        initial_budget_cny: float,
    ) -> dict:
        competition_id = competition_id.strip().lower()
        from app.services.classroom_service import ClassroomService
        if competition_id != "first-competition" and ClassroomService(self.db).membership(user.id):
            return self.status(user, competition_id, 0)
        initial_budget = Decimal("0.00")

        if competition_id == "first-competition":
            if member_emails:
                raise ValueError("This competition uses individual registration, without team members")
            with hold_advisory_xact_lock(self.db, f"competition-registration:{competition_id}:{user.id}"):
                entry = self.db.scalar(select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == competition_id,
                    CompetitionEntry.user_id == user.id,
                ))
                if entry is None:
                    entry = CompetitionEntry(
                        competition_id=competition_id, owner_kind="user", user_id=user.id,
                        display_name=user.display_name or user.username,
                        initial_budget_cny=initial_budget,
                    )
                    self.db.add(entry)
                    self.db.flush()
                if entry.confirmed_at is None:
                    entry.initial_budget_cny = initial_budget
                    entry.confirmed_at = datetime.utcnow()
                    entry.confirmed_by_user_id = user.id
                self.db.commit()
            return self.status(user, competition_id, float(initial_budget))

        normalized_emails = list(dict.fromkeys(str(email).strip().casefold() for email in member_emails))
        normalized_emails = [email for email in normalized_emails if email != user.email.casefold()]
        users_by_email: dict[str, User] = {}
        if normalized_emails:
            users_by_email = {
                candidate.email.casefold(): candidate
                for candidate in self.db.scalars(
                    select(User).where(func.lower(User.email).in_(normalized_emails))
                ).all()
            }
            missing = [email for email in normalized_emails if email not in users_by_email]
            if missing:
                raise ValueError(f"No ARC-Bench account exists for: {', '.join(missing)}")

        participants = [user, *(users_by_email[email] for email in normalized_emails)]
        lock_names = [f"team:user:{participant.id}" for participant in participants]
        lock_names.append(f"competition-registration:{competition_id}:{user.id}")
        with hold_advisory_xact_lock(self.db, *lock_names):
            # A user may have created a normal ARC-Bench team before opening
            # the official competition.  Official registration owns a fresh
            # team roster, so release that unconfirmed team first.  Never
            # dismantle a team that already has an official entry: its
            # historical submissions and membership must remain intact.
            self._release_unconfirmed_team_for_user(user.id)
            memberships = self.db.scalars(
                select(TeamMembership).where(TeamMembership.user_id.in_([participant.id for participant in participants]))
            ).all()
            if memberships:
                occupied_ids = {membership.user_id for membership in memberships}
                occupied = [participant.email for participant in participants if participant.id in occupied_ids]
                raise ValueError(f"These accounts already belong to a team: {', '.join(occupied)}")

            team = Team(name=user.username, leader_user_id=user.id)
            self.db.add(team)
            self.db.flush()
            for participant in participants:
                self.db.add(
                    TeamMembership(
                        team_id=team.id,
                        user_id=participant.id,
                        role="leader" if participant.id == user.id else "member",
                    )
                )

            entry = CompetitionEntry(
                competition_id=competition_id,
                owner_kind="team",
                team_id=team.id,
                display_name=team.name,
                initial_budget_cny=initial_budget,
                confirmed_by_user_id=user.id,
                confirmed_at=datetime.utcnow(),
            )
            self.db.add(entry)
            self.db.flush()
            for participant in participants:
                self._ensure_access_grant(competition_id, participant.id)
            self.db.commit()

        return self.status(user, competition_id, float(initial_budget))

    def require_personal_entry(self, user: User, competition_id: str) -> CompetitionEntry:
        if competition_id == "first-competition":
            with hold_advisory_xact_lock(self.db, f"competition-registration:{competition_id}:{user.id}"):
                entry = self.db.scalar(select(CompetitionEntry).where(
                    CompetitionEntry.competition_id == competition_id,
                    CompetitionEntry.user_id == user.id, CompetitionEntry.owner_kind == "user"))
                if entry is None:
                    entry = CompetitionEntry(competition_id=competition_id, owner_kind="user", user_id=user.id,
                        display_name=user.display_name or user.username, initial_budget_cny=0)
                    self.db.add(entry)
                if entry.confirmed_at is None:
                    entry.confirmed_at = datetime.utcnow()
                    entry.confirmed_by_user_id = user.id
                self.db.flush()
                return entry
        entry = self.db.scalar(select(CompetitionEntry).where(
            CompetitionEntry.competition_id == competition_id,
            CompetitionEntry.user_id == user.id,
            CompetitionEntry.owner_kind == "user",
            CompetitionEntry.confirmed_at.is_not(None),
        ))
        if entry is None:
            raise PermissionError("Confirm your competition entry before submitting or running")
        return entry

    def ensure_invited_team_entry(
        self,
        user: User,
        competition_id: str,
        initial_budget_cny: float,
    ) -> CompetitionEntry:
        """Create the participant identity for an official team event.

        The team leader creates its entry when submitting. Submissions,
        and inherited artifacts remain attached to the team identity.
        """
        competition_id = competition_id.strip().lower()
        initial_budget = Decimal("0.00")
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership is None:
            raise PermissionError("Join a team before submitting to this official competition")
        team = self.db.get(Team, membership.team_id)
        if team is None:
            raise LookupError("Your team is no longer available")
        if team.leader_user_id != user.id:
            raise PermissionError("Only the team leader can manage official competition submissions")

        entry = self.db.scalar(
            select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.team_id == team.id,
            )
        )
        if entry is None:
            entry = CompetitionEntry(
                competition_id=competition_id,
                owner_kind="team",
                team_id=team.id,
                display_name=team.name,
                initial_budget_cny=initial_budget,
                confirmed_by_user_id=user.id,
                confirmed_at=datetime.utcnow(),
            )
            self.db.add(entry)
            self.db.flush()
        elif entry.confirmed_at is None:
            entry.initial_budget_cny = initial_budget
            entry.confirmed_by_user_id = user.id
            entry.confirmed_at = datetime.utcnow()
        self.db.flush()
        return entry

    def _release_unconfirmed_team_for_user(self, user_id: str) -> None:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        if membership is None:
            return
        team = self.db.get(Team, membership.team_id)
        if team is None:
            self.db.delete(membership)
            return

        if self.team_is_confirmed(team.id):
            raise ValueError(
                "Your current team is already registered for an official competition; "
                "leave it from the team workspace before creating another team."
            )

        now = datetime.utcnow()
        if team.leader_user_id != user_id:
            leader = self.db.get(User, team.leader_user_id)
            leaving_user = self.db.get(User, user_id)
            self.db.delete(membership)
            if leader is not None and leaving_user is not None:
                self.db.add(
                    Notification(
                        user_id=leader.id,
                        kind="team_member_left",
                        title=f"Member left {team.name}",
                        body=f"{leaving_user.username} left the team to enter an official competition.",
                    )
                )
            return

        memberships = self.db.scalars(
            select(TeamMembership).where(TeamMembership.team_id == team.id)
        ).all()
        for item in memberships:
            if item.user_id != user_id:
                self.db.add(
                    Notification(
                        user_id=item.user_id,
                        kind="team_disbanded",
                        title=f"{team.name} was closed for official registration",
                        body="The team was closed when its leader entered an official competition.",
                    )
                )
            self.db.delete(item)
        for request in self.db.scalars(
            select(TeamJoinRequest).where(
                TeamJoinRequest.team_id == team.id,
                TeamJoinRequest.status == "PENDING",
            )
        ).all():
            request.status = "DECLINED"
            request.resolved_at = now
        team.is_active = False
        team.updated_at = now

    def ensure_joined_member_entitlements(self, team_id: str, user_id: str) -> None:
        entries = self.db.scalars(
            select(CompetitionEntry).where(
                CompetitionEntry.team_id == team_id,
                CompetitionEntry.confirmed_at.is_not(None),
            )
        ).all()
        for entry in entries:
            self._ensure_access_grant(entry.competition_id, user_id)

    def team_budgets_for_user(self, user_id: str) -> list[dict]:
        return []

    def team_is_confirmed(self, team_id: str) -> bool:
        return bool(
            self.db.scalar(
                select(CompetitionEntry.id).where(
                    CompetitionEntry.team_id == team_id,
                    CompetitionEntry.confirmed_at.is_not(None),
                ).limit(1)
            )
        )

    def _ensure_access_grant(self, competition_id: str, user_id: str) -> None:
        grant = self.db.scalar(
            select(CompetitionAccessGrant).where(
                CompetitionAccessGrant.user_id == user_id,
                CompetitionAccessGrant.competition_id == competition_id,
            )
        )
        if grant is None:
            self.db.add(
                CompetitionAccessGrant(
                    user_id=user_id,
                    competition_id=competition_id,
                    granted_by="official_competition_confirmation",
                )
            )

    def _confirmed_entry(self, competition_id: str, team_id: str) -> CompetitionEntry | None:
        return self.db.scalar(
            select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.team_id == team_id,
                CompetitionEntry.confirmed_at.is_not(None),
            )
        )

    @staticmethod
    def _status_payload(
        competition_id: str,
        initial_budget: Decimal,
        *,
        registered: bool,
        can_confirm: bool,
        team: Team | None = None,
        is_team_leader: bool = False,
        remaining_budget: Decimal | None = None,
        message: str | None = None,
    ) -> dict:
        return {
            "competition_id": competition_id,
            "competition_type": "official",
            "registered": registered,
            "can_confirm": can_confirm,
            "team_id": team.id if team else None,
            "team_name": team.name if team else None,
            "is_team_leader": is_team_leader,
            "initial_budget_cny": 0.0,
            "remaining_budget_cny": None,
            "currency": "CNY",
            "message": message,
        }
