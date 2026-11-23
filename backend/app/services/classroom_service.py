"""Class membership, roster reconciliation and server-side participation policy."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.classroom import Classroom, ClassroomMembership, ClassroomTrack
from app.models.competition_account import CompetitionEntry, Team, TeamJoinRequest, TeamMembership
from app.models.user import User
from app.services.lock_service import hold_advisory_xact_lock

log = logging.getLogger(__name__)
ROSTER_PATH = Path(__file__).resolve().parents[1] / "resources" / "classrooms.json"


class ClassroomService:
    def __init__(self, db: Session):
        self.db = db

    def membership(self, user_id: str) -> ClassroomMembership | None:
        return self.db.get(ClassroomMembership, user_id)

    def track_ids(self, user_id: str) -> list[str]:
        membership = self.membership(user_id)
        if membership is None:
            return []
        return list(self.db.scalars(select(ClassroomTrack.competition_id).where(
            ClassroomTrack.classroom_id == membership.classroom_id)).all())

    def require_track(self, user_id: str, competition_id: str) -> None:
        if competition_id == "first-competition":
            return
        if self.membership(user_id) and competition_id not in self.track_ids(user_id):
            raise PermissionError("This track is not enabled for your class")

    def require_leader(self, user_id: str, competition_id: str) -> Team | None:
        if competition_id == "first-competition":
            return None
        if self.membership(user_id) is None:
            return None
        self.require_track(user_id, competition_id)
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        team = self.db.get(Team, membership.team_id) if membership else None
        if team is None or not team.is_active or team.source_provider != "classroom":
            raise PermissionError("Your class team has not been assigned; contact the course staff")
        if team.leader_user_id != user_id:
            raise PermissionError("Only the class team leader can submit or manage competition runs")
        return team

    def ensure_entry(self, user_id: str, competition_id: str) -> CompetitionEntry | None:
        team = self.require_leader(user_id, competition_id)
        if team is None:
            return None
        return self._ensure_team_entry(team, competition_id)

    def _ensure_team_entry(self, team: Team, competition_id: str) -> CompetitionEntry:
        entry = self.db.scalar(select(CompetitionEntry).where(
            CompetitionEntry.team_id == team.id, CompetitionEntry.competition_id == competition_id))
        if entry is None:
            entry = CompetitionEntry(competition_id=competition_id, owner_kind="team", team_id=team.id,
                display_name=team.name, initial_budget_cny=0,
                confirmed_at=datetime.utcnow(), confirmed_by_user_id=team.leader_user_id)
            self.db.add(entry)
            self.db.flush()
        else:
            entry.display_name = team.name
            if entry.confirmed_at is None:
                entry.confirmed_at = datetime.utcnow()
                entry.confirmed_by_user_id = team.leader_user_id
        return entry

    def sync_rosters(self, *, commit: bool = True) -> dict:
        """Idempotent startup/registration sync. Never create accounts or credit."""
        definitions = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
        result = {"assigned_users": 0, "teams": 0, "retired_teams": 0, "missing_accounts": 0, "unmatched_members": [], "conflicts": []}
        with hold_advisory_xact_lock(self.db, "classroom-roster-sync"):
            users = {u.email.strip().casefold(): u for u in self.db.scalars(select(User)).all()}
            users_by_student_id = {}
            for user in users.values():
                student_id = str(user.student_id or "").strip()
                if student_id:
                    users_by_student_id.setdefault(student_id, {})[user.id] = user
            for definition in definitions:
                classroom = self.db.get(Classroom, definition["id"])
                if classroom is None:
                    classroom = Classroom(id=definition["id"], name=definition["name"], description=definition["description"])
                    self.db.add(classroom)
                    self.db.flush()
                elif not classroom.description or classroom.description.endswith("成员名单待补充。"):
                    # Fill an empty-class placeholder when its roster is supplied;
                    # preserve descriptions that staff have already customized.
                    classroom.description = definition["description"]
                # Existing names/descriptions and team names remain editable by staff.
                tracks = set(definition["tracks"])
                self.db.execute(delete(ClassroomTrack).where(ClassroomTrack.classroom_id == classroom.id,
                    ClassroomTrack.competition_id.not_in(tracks)))
                for competition_id in tracks:
                    if self.db.get(ClassroomTrack, (classroom.id, competition_id)) is None:
                        self.db.add(ClassroomTrack(classroom_id=classroom.id, competition_id=competition_id))
                matched = {}
                used_users = set()
                for person in definition["members"]:
                    candidates = [users[e.strip().casefold()] for e in person["emails"] if e.strip().casefold() in users]
                    if not candidates:
                        student_matches = {
                            user_id: user
                            for student_id in person.get("student_ids", [])
                            for user_id, user in users_by_student_id.get(str(student_id).strip(), {}).items()
                        }
                        if len(student_matches) > 1:
                            result["conflicts"].append(f"ambiguous student ID for {person['name']}; staff review required")
                            continue
                        candidates = list(student_matches.values())
                    if not candidates:
                        result["missing_accounts"] += 1
                        result["unmatched_members"].append({
                            "classroom_id": classroom.id, "name": person["name"],
                            "team_key": person.get("team_key"),
                        })
                        continue
                    # Prefer the student-list address, then the team-list alias.
                    roster_names = {person["name"], *person.get("aliases", [])}
                    assigned = [u for u in candidates if (m := self.membership(u.id)) is not None
                                and m.classroom_id == classroom.id and m.roster_name in roster_names]
                    user = assigned[0] if assigned else candidates[0]
                    if user.id in used_users:
                        result["conflicts"].append(f"duplicate roster identity: {person['name']}")
                        continue
                    used_users.add(user.id)
                    if len({u.id for u in candidates}) > 1:
                        result["conflicts"].append(f"multiple accounts for {person['name']}; existing assignment or preferred roster email used")
                    membership = self.membership(user.id)
                    if membership and membership.classroom_id != classroom.id:
                        result["conflicts"].append(f"account already belongs to another class: {person['name']}")
                        continue
                    current_team = self.db.scalar(select(Team).join(TeamMembership, TeamMembership.team_id == Team.id)
                        .where(TeamMembership.user_id == user.id))
                    expected_team = f"{classroom.id}:{person['team_key']}" if person.get("team_key") else None
                    allowed_previous = {f"{classroom.id}:{key}" for key in person.get("previous_team_keys", [])}
                    if (current_team and current_team.source_provider == "classroom" and expected_team
                            and current_team.source_team_id != expected_team
                            and current_team.source_team_id not in allowed_previous):
                        result["conflicts"].append(f"managed team differs from roster: {person['name']}; staff review required")
                        continue
                    if membership is None:
                        self.db.add(ClassroomMembership(user_id=user.id, classroom_id=classroom.id, roster_name=person["name"]))
                        result["assigned_users"] += 1
                    elif membership.roster_name != person["name"]:
                        membership.roster_name = person["name"]
                    matched[person["name"]] = (user, person)
                self.db.flush()
                for group in definition["teams"]:
                    leader_match = matched.get(group["leader_name"])
                    if leader_match is None:
                        # No substitute captain: defer until the designated leader registers.
                        continue
                    leader = leader_match[0]
                    source_id = f"{classroom.id}:{group['key']}"
                    team = self.db.scalar(select(Team).where(Team.source_provider == "classroom", Team.source_team_id == source_id))
                    if team is None:
                        team = Team(name=group["name"], leader_user_id=leader.id,
                            source_provider="classroom", source_team_id=source_id)
                        self.db.add(team)
                        self.db.flush()
                    team.leader_user_id = leader.id
                    team.is_active = True
                    for user, person in matched.values():
                        if person.get("team_key") == group["key"]:
                            with hold_advisory_xact_lock(self.db, f"team:user:{user.id}"):
                                self._assign_team(user, team, leader.id, allowed_previous_team_ids={
                                    f"{classroom.id}:{key}" for key in person.get("previous_team_keys", [])
                                })
                    for competition_id in tracks:
                        if competition_id == "first-competition":
                            continue
                        entry = self._ensure_team_entry(team, competition_id)
                        # Rebind existing course submissions/results to the assigned
                        # team identity, retaining scores, usage and billing ledgers.
                        from app.models.submission import Submission
                        from app.models.run import Run
                        member_ids = select(TeamMembership.user_id).where(TeamMembership.team_id == team.id)
                        for model in (Submission, Run):
                            self.db.execute(update(model).where(model.user_id.in_(member_ids),
                                model.catalog == "competition", model.competition_id == competition_id,
                                or_(model.competition_entry_id.is_(None), model.competition_entry_id != entry.id,
                                    model.competition_owner_display_name.is_(None), model.competition_owner_display_name != team.name)).values(
                                    competition_entry_id=entry.id, competition_owner_display_name=team.name))
                    result["teams"] += 1
                self.db.flush()
                for key in definition.get("retired_team_keys", []):
                    retired = self.db.scalar(select(Team).where(
                        Team.source_provider == "classroom",
                        Team.source_team_id == f"{classroom.id}:{key}"))
                    if retired is None:
                        continue
                    remaining = self.db.scalar(select(TeamMembership.id).where(
                        TeamMembership.team_id == retired.id).limit(1))
                    if remaining is not None:
                        result["conflicts"].append(f"retired team still has members: {classroom.id}:{key}; migration must finish first")
                        continue
                    # Remove the group from class views/counts while preserving
                    # historical entries and transactions that reference its ID.
                    retired.is_active = False
                    for request in self.db.scalars(select(TeamJoinRequest).where(
                        TeamJoinRequest.team_id == retired.id,
                        TeamJoinRequest.status == "PENDING")).all():
                        request.status = "DECLINED"
                        request.resolved_at = datetime.utcnow()
                    result["retired_teams"] += 1
                self.db.flush()
            # Smoke is individual, including for students in managed teams.
            # Restore historical snapshots/runs to their submitting account.
            from app.models.submission import Submission
            from app.models.run import Run
            from app.services.competition_registration_service import CompetitionRegistrationService
            smoke_users = set(self.db.scalars(select(Submission.user_id).where(
                Submission.catalog == "competition", Submission.competition_id == "first-competition")).all())
            for user_id in smoke_users:
                user = self.db.get(User, user_id)
                if user is None:
                    continue
                entry = CompetitionRegistrationService(self.db).require_personal_entry(user, "first-competition")
                for model in (Submission, Run):
                    self.db.execute(update(model).where(model.user_id == user_id,
                        model.catalog == "competition", model.competition_id == "first-competition").values(
                            competition_entry_id=entry.id, competition_owner_display_name=entry.display_name))
            if commit:
                self.db.commit()
        for conflict in result["conflicts"]:
            log.warning("Class roster sync: %s", conflict)
        log.info("Class roster sync: assigned=%s teams=%s missing=%s conflicts=%s",
                 result["assigned_users"], result["teams"], result["missing_accounts"], len(result["conflicts"]))
        return result

    def _assign_team(self, user: User, team: Team, leader_id: str, *, allowed_previous_team_ids=()) -> None:
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user.id))
        if membership and membership.team_id != team.id:
            previous = self.db.get(Team, membership.team_id)
            if (previous and previous.source_provider == "classroom"
                    and previous.source_team_id not in allowed_previous_team_ids):
                raise ValueError("A class roster would move an account between managed teams; correct the roster first")
            self.db.delete(membership)
            self.db.flush()
            if previous and previous.leader_user_id == user.id:
                remaining = self.db.scalar(select(TeamMembership).where(TeamMembership.team_id == previous.id)
                    .order_by(TeamMembership.created_at, TeamMembership.user_id).limit(1))
                if remaining:
                    previous.leader_user_id = remaining.user_id
                    remaining.role = "leader"
                else:
                    previous.is_active = False
            membership = None
        if membership is None:
            membership = TeamMembership(team_id=team.id, user_id=user.id)
            self.db.add(membership)
        membership.role = "leader" if user.id == leader_id else "member"
        for request in self.db.scalars(select(TeamJoinRequest).where(
            TeamJoinRequest.user_id == user.id, TeamJoinRequest.status == "PENDING")).all():
            request.status = "DECLINED"
            request.resolved_at = datetime.utcnow()
        self.db.flush()

    def my_class(self, user_id: str) -> dict:
        membership = self.membership(user_id)
        if membership is None:
            return {"classroom": None, "team": None, "tracks": [], "teams": [], "is_team_leader": False}
        classroom = self.db.get(Classroom, membership.classroom_id)
        class_user_ids = select(ClassroomMembership.user_id).where(ClassroomMembership.classroom_id == classroom.id)
        teams = self.db.scalars(select(Team).join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(TeamMembership.user_id.in_(class_user_ids), Team.source_provider == "classroom", Team.is_active.is_(True)).distinct()
            .order_by(Team.created_at, Team.id)).all()
        from app.services.requirement_catalog import RequirementCatalogService
        catalog = {item.id: item for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()}
        tracks = [{"id": track_id, "title": catalog[track_id].title if track_id in catalog else track_id,
                   "status": catalog[track_id].status if track_id in catalog else "unavailable"} for track_id in self.track_ids(user_id)]
        my_team = self.db.scalar(select(Team).join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(TeamMembership.user_id == user_id, Team.is_active.is_(True), Team.source_provider == "classroom"))
        rows = []
        for team in teams:
            members = self.db.execute(select(User, ClassroomMembership.roster_name).join(TeamMembership, TeamMembership.user_id == User.id)
                .join(ClassroomMembership, ClassroomMembership.user_id == User.id).where(TeamMembership.team_id == team.id)
                .order_by(TeamMembership.role.desc(), ClassroomMembership.roster_name)).all()
            rows.append({"id": team.id, "name": team.name, "leader_user_id": team.leader_user_id,
                "members": [{"user_id": user.id, "name": name or user.display_name or user.username,
                             "is_leader": user.id == team.leader_user_id} for user, name in members]})
        mine = next((row for row in rows if my_team and row["id"] == my_team.id), None)
        return {"classroom": {"id": classroom.id, "name": classroom.name, "description": classroom.description,
                "member_count": self.db.scalar(select(func.count()).select_from(ClassroomMembership).where(
                    ClassroomMembership.classroom_id == classroom.id)), "team_count": len(teams)},
                "team": mine, "tracks": tracks, "teams": rows,
                "is_team_leader": bool(my_team and my_team.leader_user_id == user_id)}

    def list_classroom_summaries(self) -> list[dict]:
        """Return every configured class summary for site administrators."""
        classrooms = self.db.scalars(select(Classroom).order_by(Classroom.name)).all()
        result = []
        for classroom in classrooms:
            member_count = self.db.scalar(select(func.count()).select_from(ClassroomMembership).where(
                ClassroomMembership.classroom_id == classroom.id)) or 0
            team_count = self.db.scalar(select(func.count()).select_from(Team).where(
                Team.source_provider == "classroom", Team.source_team_id.like(f"{classroom.id}:%"),
                Team.is_active.is_(True))) or 0
            result.append({"id": classroom.id, "name": classroom.name, "description": classroom.description,
                           "member_count": member_count, "team_count": team_count})
        return result

    def filter_leaderboard(self, user_id: str | None, classroom_id: str, competition_id: str | None, entries):
        membership = self.membership(user_id) if user_id else None
        user = self.db.get(User, user_id) if user_id else None
        is_admin = bool(user is not None and user.is_admin)
        if not is_admin and (membership is None or membership.classroom_id != classroom_id):
            raise PermissionError("Only class members can view this class leaderboard")
        if is_admin:
            if self.db.get(Classroom, classroom_id) is None:
                raise PermissionError("Unknown class")
            permitted = set(self.db.scalars(select(ClassroomTrack.competition_id).where(
                ClassroomTrack.classroom_id == classroom_id)).all())
        else:
            permitted = set(self.track_ids(user_id))
        if competition_id and competition_id not in permitted:
            raise PermissionError("This track is not enabled for this class")
        class_users = select(ClassroomMembership.user_id).where(ClassroomMembership.classroom_id == classroom_id)
        entry_ids = set(self.db.scalars(select(CompetitionEntry.id).join(TeamMembership,
            TeamMembership.team_id == CompetitionEntry.team_id).where(TeamMembership.user_id.in_(class_users),
            CompetitionEntry.competition_id.in_(permitted))).all())
        entry_ids.update(self.db.scalars(select(CompetitionEntry.id).where(
            CompetitionEntry.user_id.in_(class_users), CompetitionEntry.owner_kind == "user",
            CompetitionEntry.competition_id == "first-competition")).all())
        # Filter by participant identity, never by an email list sent to the browser.
        from app.models.run import Run
        source_ids = {run_id for entry in entries for run_id in entry.source_run_ids}
        allowed_run_ids = set(self.db.scalars(select(Run.id).where(
            Run.id.in_(source_ids), Run.competition_entry_id.in_(entry_ids), Run.competition_id.in_(permitted))).all())
        return [entry for entry in entries if entry.source_run_ids and
                all(run_id in allowed_run_ids for run_id in entry.source_run_ids)]
