from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.competition_account import CompetitionBudgetTransaction, CompetitionEntry, Team, TeamJoinRequest, TeamMembership
from app.models.notification import Notification
from app.models.user import User
from app.services.lock_service import hold_advisory_xact_lock
from app.services.competition_registration_service import CompetitionRegistrationService


class TeamService:
    def __init__(self, db: Session):
        self.db = db

    def get_my_team_payload(self, user_id: str) -> dict:
        source_managed = self._is_source_managed_user(user_id)
        membership = self._membership_for_user(user_id)
        pending = self.db.scalar(
            select(TeamJoinRequest)
            .where(TeamJoinRequest.user_id == user_id, TeamJoinRequest.status == "PENDING")
            .order_by(TeamJoinRequest.created_at.desc())
            .limit(1)
        )
        if membership is None:
            return {
                "team": None,
                "members": [],
                "incoming_requests": [],
                "pending_request": self._request_summary(pending) if pending else None,
                "source_managed": source_managed,
                "competition_budgets": CompetitionRegistrationService(self.db).team_budgets_for_user(user_id),
            }
        team = self.db.get(Team, membership.team_id)
        if team is None:
            return {"team": None, "members": [], "incoming_requests": [], "pending_request": None, "source_managed": source_managed, "competition_budgets": CompetitionRegistrationService(self.db).team_budgets_for_user(user_id)}
        members = self.db.scalars(
            select(TeamMembership).where(TeamMembership.team_id == team.id).order_by(TeamMembership.created_at)
        ).all()
        requests: list[TeamJoinRequest] = []
        if team.leader_user_id == user_id:
            requests = self.db.scalars(
                select(TeamJoinRequest)
                .where(TeamJoinRequest.team_id == team.id, TeamJoinRequest.status == "PENDING")
                .order_by(TeamJoinRequest.created_at)
            ).all()
        return {
            "team": self._team_summary(team),
            "members": [self._member_summary(item) for item in members],
            "incoming_requests": [self._request_summary(item) for item in requests],
            "pending_request": None,
            "source_managed": source_managed,
            "competition_budgets": CompetitionRegistrationService(self.db).team_budgets_for_user(user_id),
        }

    def create_team(self, user_id: str, name: str, slogan: str | None = None) -> dict:
        self._require_local_team_management(user_id)
        normalized_name = " ".join(name.strip().split())
        if len(normalized_name) < 2 or len(normalized_name) > 120:
            raise ValueError("Team name must be between 2 and 120 characters")
        normalized_slogan = " ".join((slogan or "").strip().split()) or None
        if normalized_slogan and len(normalized_slogan) > 240:
            raise ValueError("Team slogan must be 240 characters or fewer")
        with hold_advisory_xact_lock(self.db, f"team:user:{user_id}", f"team:name:{normalized_name.casefold()}"):
            if self._membership_for_user(user_id):
                raise ValueError("Leave your current team before creating another one")
            user = self._user(user_id)
            if self.db.scalar(select(Team.id).where(func.lower(Team.name) == normalized_name.lower())):
                raise ValueError("That team name is already in use")
            team = Team(name=normalized_name, slogan=normalized_slogan, leader_user_id=user.id)
            self.db.add(team)
            self.db.flush()
            self.db.add(TeamMembership(team_id=team.id, user_id=user.id, role="leader"))
            self.db.commit()
        return self.get_my_team_payload(user_id)

    def rename_team(self, user_id: str, name: str) -> dict:
        normalized_name = " ".join(name.strip().split())
        if len(normalized_name) < 2 or len(normalized_name) > 120:
            raise ValueError("Team name must be between 2 and 120 characters")
        membership = self._membership_for_user(user_id)
        if membership is None:
            raise ValueError("You are not currently in a team")
        team = self.db.get(Team, membership.team_id)
        if team is None or not team.is_active:
            raise LookupError("Team not found")
        if team.leader_user_id != user_id:
            raise PermissionError("Only the team leader can rename the team")
        with hold_advisory_xact_lock(self.db, f"team:{team.id}", f"team:name:{normalized_name.casefold()}"):
            duplicate = self.db.scalar(
                select(Team.id).where(
                    func.lower(Team.name) == normalized_name.lower(),
                    Team.id != team.id,
                )
            )
            if duplicate:
                raise ValueError("That team name is already in use")
            team.name = normalized_name
            team.updated_at = datetime.utcnow()
            for entry in self.db.scalars(
                select(CompetitionEntry).where(CompetitionEntry.team_id == team.id)
            ).all():
                entry.display_name = normalized_name
            self.db.commit()
        return self.get_my_team_payload(user_id)

    def request_join(self, user_id: str, team_id: str) -> dict:
        self._require_local_team_management(user_id)
        normalized_team_id = team_id.strip()
        with hold_advisory_xact_lock(self.db, f"team:user:{user_id}", f"team:{normalized_team_id}"):
            if self._membership_for_user(user_id):
                raise ValueError("Leave your current team before requesting to join another team")
            team = self.db.get(Team, normalized_team_id)
            if team is None or not team.is_active:
                raise LookupError("Team not found")
            if team.source_provider == "classroom":
                raise ValueError("Class teams are assigned by course staff")
            if team.leader_user_id == user_id:
                raise ValueError("You already lead this team")
            pending = self.db.scalar(
                select(TeamJoinRequest).where(
                    TeamJoinRequest.user_id == user_id,
                    TeamJoinRequest.status == "PENDING",
                )
            )
            if pending:
                raise ValueError("Your request is already awaiting a team leader's decision")
            existing = self.db.scalar(
                select(TeamJoinRequest).where(
                    TeamJoinRequest.team_id == normalized_team_id,
                    TeamJoinRequest.user_id == user_id,
                )
            )
            if existing:
                existing.status = "PENDING"
                existing.created_at = datetime.utcnow()
                existing.resolved_at = None
                request = existing
            else:
                request = TeamJoinRequest(team_id=team.id, user_id=user_id)
                self.db.add(request)
            self.db.flush()
            requester = self._user(user_id)
            self.db.add(
                Notification(
                    user_id=team.leader_user_id,
                    team_join_request_id=request.id,
                    kind="team_join_request",
                    title=f"Join request for {team.name}",
                    body=f"{requester.username} asked to join your team.",
                )
            )
            self.db.commit()
            self.db.refresh(request)
        return self._request_summary(request)

    def get_join_request(self, viewer_user_id: str, request_id: str) -> dict:
        request = self.db.get(TeamJoinRequest, request_id)
        if request is None:
            raise LookupError("Join request not found")
        team = self.db.get(Team, request.team_id)
        if team is None:
            raise LookupError("Team not found")
        if viewer_user_id not in {request.user_id, team.leader_user_id}:
            raise PermissionError("You cannot view this join request")
        return self._request_summary(request)

    def resolve_request(self, leader_user_id: str, request_id: str, *, accept: bool) -> dict:
        self._require_local_team_management(leader_user_id)
        request = self.db.get(TeamJoinRequest, request_id)
        if request is None:
            raise LookupError("Join request not found")
        with hold_advisory_xact_lock(self.db, f"team:request:{request_id}", f"team:user:{request.user_id}"):
            self.db.refresh(request)
            team = self.db.get(Team, request.team_id)
            if team is None or not team.is_active:
                raise LookupError("Team not found")
            if team.leader_user_id != leader_user_id:
                raise PermissionError("Only the team leader can review join requests")
            if request.status != "PENDING":
                raise ValueError("This join request has already been decided")
            if accept:
                self._require_local_team_management(request.user_id)
                if self._membership_for_user(request.user_id):
                    request.status = "DECLINED"
                    request.resolved_at = datetime.utcnow()
                    self.db.commit()
                    raise ValueError("This user is already in a team")
                self.db.add(TeamMembership(team_id=team.id, user_id=request.user_id, role="member"))
                CompetitionRegistrationService(self.db).ensure_joined_member_entitlements(team.id, request.user_id)
                request.status = "ACCEPTED"
                message = f"Your request to join {team.name} was accepted."
                kind = "team_join_accepted"
            else:
                request.status = "DECLINED"
                message = f"Your request to join {team.name} was declined."
                kind = "team_join_declined"
            request.resolved_at = datetime.utcnow()
            self.db.add(
                Notification(
                    user_id=request.user_id,
                    team_join_request_id=request.id,
                    kind=kind,
                    title="Team request updated",
                    body=message,
                )
            )
            self.db.commit()
        return self.get_my_team_payload(leader_user_id)

    def leave_team(self, user_id: str) -> None:
        self._require_local_team_management(user_id)
        membership = self._membership_for_user(user_id)
        if membership is None:
            raise ValueError("You are not currently in a team")
        team = self.db.get(Team, membership.team_id)
        if team is None:
            self.db.delete(membership)
            self.db.commit()
            return
        with hold_advisory_xact_lock(self.db, f"team:{team.id}", f"team:user:{user_id}"):
            if team.leader_user_id == user_id:
                self._disband_team(team)
            else:
                leader = self._user(team.leader_user_id)
                leaving_user = self._user(user_id)
                self.db.add(
                    Notification(
                        user_id=leader.id,
                        kind="team_member_left",
                        title=f"Member left {team.name}",
                        body=f"{leaving_user.username} left your team.",
                    )
                )
                self.db.delete(membership)
            self.db.commit()

    def remove_member(self, leader_user_id: str, member_user_id: str) -> dict:
        self._require_local_team_management(leader_user_id)
        leader_membership = self._membership_for_user(leader_user_id)
        if leader_membership is None:
            raise ValueError("You are not currently in a team")
        team = self.db.get(Team, leader_membership.team_id)
        if team is None or not team.is_active:
            raise LookupError("Team not found")
        if team.leader_user_id != leader_user_id:
            raise PermissionError("Only the team leader can remove members")
        if member_user_id == leader_user_id:
            raise ValueError("Disband the team instead of removing the team leader")

        with hold_advisory_xact_lock(self.db, f"team:{team.id}", f"team:user:{member_user_id}"):
            membership = self.db.scalar(
                select(TeamMembership).where(
                    TeamMembership.team_id == team.id,
                    TeamMembership.user_id == member_user_id,
                )
            )
            if membership is None:
                raise LookupError("Team member not found")
            member = self._user(member_user_id)
            self.db.delete(membership)
            self.db.add(
                Notification(
                    user_id=member.id,
                    kind="team_member_removed",
                    title=f"Removed from {team.name}",
                    body=f"The team leader removed you from {team.name}.",
                )
            )
            self.db.commit()
        return self.get_my_team_payload(leader_user_id)

    def _disband_team(self, team: Team) -> None:
        memberships = self.db.scalars(
            select(TeamMembership).where(TeamMembership.team_id == team.id)
        ).all()
        for item in memberships:
            if item.user_id != team.leader_user_id:
                self.db.add(
                    Notification(
                        user_id=item.user_id,
                        kind="team_disbanded",
                        title=f"{team.name} was disbanded",
                        body="The team leader disbanded the team. You can now create or join another team.",
                    )
                )
            self.db.delete(item)
        now = datetime.utcnow()
        for request in self.db.scalars(
            select(TeamJoinRequest).where(TeamJoinRequest.team_id == team.id)
        ).all():
            self.db.delete(request)

        # A plain team with no competition history can be physically deleted.
        # Official/historical teams are retained as inactive rows because
        # competition entries and budget transactions reference their IDs.
        has_competition_history = bool(
            self.db.scalar(select(CompetitionEntry.id).where(CompetitionEntry.team_id == team.id).limit(1))
            or self.db.scalar(
                select(CompetitionBudgetTransaction.id)
                .where(CompetitionBudgetTransaction.team_id == team.id)
                .limit(1)
            )
        )
        if has_competition_history:
            team.is_active = False
            team.updated_at = now
        else:
            self.db.delete(team)

    def _membership_for_user(self, user_id: str) -> TeamMembership | None:
        return self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))

    def _is_source_managed_user(self, user_id: str) -> bool:
        from app.services.classroom_service import ClassroomService
        return ClassroomService(self.db).membership(user_id) is not None

    def _require_local_team_management(self, user_id: str) -> None:
        if self._is_source_managed_user(user_id):
            raise ValueError("Class teams are managed by course staff; members cannot change teams or leave")

    def _user(self, user_id: str) -> User:
        user = self.db.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        return user

    def _team_summary(self, team: Team) -> dict:
        leader = self._user(team.leader_user_id)
        member_count = int(
            self.db.scalar(select(func.count()).select_from(TeamMembership).where(TeamMembership.team_id == team.id)) or 0
        )
        return {
            "id": team.id,
            "name": team.name,
            "slogan": team.slogan,
            "leader_user_id": team.leader_user_id,
            "leader_username": leader.username,
            "member_count": member_count,
            "source_provider": team.source_provider,
            "source_team_id": team.source_team_id,
            "github_repo": team.github_repo,
            "model_name": team.model_name,
            "harness": team.harness,
            "created_at": team.created_at,
        }

    def _member_summary(self, membership: TeamMembership) -> dict:
        user = self._user(membership.user_id)
        return {
            "user_id": user.id,
            "email": user.email,
            "username": user.username,
            "display_name": user.display_name,
            "real_name": user.real_name,
            "student_id": user.student_id,
            "avatar_url": user.avatar_url,
            "role": membership.role,
            "created_at": membership.created_at,
        }

    def _request_summary(self, request: TeamJoinRequest) -> dict:
        user = self._user(request.user_id)
        team = self.db.get(Team, request.team_id)
        return {
            "id": request.id,
            "team_id": request.team_id,
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "real_name": user.real_name,
            "student_id": user.student_id,
            "avatar_url": user.avatar_url,
            "team_name": team.name if team else "Unknown team",
            "status": request.status,
            "created_at": request.created_at,
        }
