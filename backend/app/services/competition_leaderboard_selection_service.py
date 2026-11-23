"""Resolve latest-by-default leaderboard snapshots and explicit preferences."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competition_account import CompetitionEntry, Team, TeamMembership
from app.models.competition_leaderboard_selection import CompetitionLeaderboardSelection
from app.models.submission import Submission
from app.services.lock_service import hold_advisory_xact_lock


class CompetitionLeaderboardSelectionService:
    def __init__(self, db: Session):
        self.db = db

    def resolve(self, snapshots) -> tuple[dict[str, Submission], set[str]]:
        snapshots = [snapshot for snapshot in snapshots if snapshot.deprecated_at is None]
        if not snapshots:
            return {}, set()
        selected = {}
        by_id = {snapshot.id: snapshot for snapshot in snapshots}
        for snapshot in snapshots:
            participant = str(snapshot.competition_entry_id or snapshot.user_id or "")
            previous = selected.get(participant)
            if participant and (previous is None or (snapshot.created_at, snapshot.id) > (previous.created_at, previous.id)):
                selected[participant] = snapshot
        preferences = self.db.scalars(
            select(CompetitionLeaderboardSelection).where(
                CompetitionLeaderboardSelection.competition_id.in_({s.competition_id for s in snapshots}),
                CompetitionLeaderboardSelection.participant_id.in_(list(selected)),
            )
        ).all()
        manual_ids = set()
        for preference in preferences:
            snapshot = by_id.get(preference.submission_id)
            if snapshot is None or snapshot.competition_id != preference.competition_id:
                continue
            if str(snapshot.competition_entry_id or snapshot.user_id or "") != preference.participant_id:
                continue
            selected[preference.participant_id] = snapshot
            manual_ids.add(snapshot.id)
        return selected, manual_ids

    def set_selection(self, competition_id: str, user_id: str, submission_id: str | None) -> None:
        competition_id = competition_id.strip().lower()
        from app.services.classroom_service import ClassroomService
        ClassroomService(self.db).require_leader(user_id, competition_id)
        membership = self.db.scalar(select(TeamMembership).where(TeamMembership.user_id == user_id))
        entry = self.db.scalar(select(CompetitionEntry).where(
            CompetitionEntry.competition_id == competition_id,
            CompetitionEntry.team_id == membership.team_id,
        )) if membership and competition_id != "first-competition" else None
        if entry is None:
            entry = self.db.scalar(select(CompetitionEntry).where(
                CompetitionEntry.competition_id == competition_id,
                CompetitionEntry.user_id == user_id,
            ))
        if entry and entry.team_id:
            team = self.db.get(Team, entry.team_id)
            if team is None or team.leader_user_id != user_id:
                raise PermissionError("Only the team leader can choose the leaderboard submission")
        participant_id = entry.id if entry else user_id
        with hold_advisory_xact_lock(self.db, f"leaderboard-selection:{competition_id}:{participant_id}"):
            if submission_id is not None:
                snapshot = self.db.scalar(select(Submission).where(
                    Submission.id == submission_id,
                    Submission.catalog == "competition",
                    Submission.competition_id == competition_id,
                    Submission.deprecated_at.is_(None),
                ).with_for_update())
                if snapshot is None:
                    raise LookupError("Active competition submission not found")
                if str(snapshot.competition_entry_id or snapshot.user_id or "") != participant_id:
                    raise PermissionError("You cannot select another participant's submission")
            preference = self.db.get(CompetitionLeaderboardSelection, (competition_id, participant_id))
            if submission_id is None:
                if preference is not None:
                    self.db.delete(preference)
            elif preference is None:
                self.db.add(CompetitionLeaderboardSelection(
                    competition_id=competition_id,
                    participant_id=participant_id,
                    submission_id=submission_id,
                    selected_by_user_id=user_id,
                ))
            else:
                preference.submission_id = submission_id
                preference.selected_by_user_id = user_id
                preference.updated_at = datetime.utcnow()
            self.db.commit()
