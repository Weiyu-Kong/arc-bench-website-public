from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competition_account import CompetitionAccessGrant, CompetitionEntry, TeamMembership
from app.models.user import User
from app.services.lock_service import hold_advisory_xact_lock


class CompetitionAccessService:
    """Single server-side policy for competition visibility and participation."""

    HACKATHON_ID = "hackathon"
    GLOBAL_GRANT = "*"
    LISTED_COMPETITION_IDS = frozenset({
        "smoke",
        "smoke-evolution",
        "first-competition",
        "ticket-booking-evolution",
        "arc-bench-web",
        "arc-bench-evolution",
        "arc-bench-lite",
        "arc-bench-lite-evolution",
        "hackathon",
        "hackathon-evolution",
    })

    def __init__(self, db: Session):
        self.db = db

    @classmethod
    def is_hackathon_competition(cls, competition_id: str) -> bool:
        normalized_id = competition_id.strip().lower()
        return normalized_id == cls.HACKATHON_ID or normalized_id.startswith(f"{cls.HACKATHON_ID}-")

    @classmethod
    def is_listed_competition(cls, competition_id: str) -> bool:
        """Return whether a competition is shown in the ARC-Bench catalog UI."""
        return competition_id.strip().lower() in cls.LISTED_COMPETITION_IDS

    def can_access(self, user: User | None, competition_id: str) -> bool:
        normalized_id = competition_id.strip().lower()
        if not normalized_id:
            return False
        if normalized_id == "first-competition":
            return True
        if user is not None and user.is_admin:
            return True
        if user is not None and normalized_id != self.GLOBAL_GRANT:
            from app.services.classroom_service import ClassroomService
            classes = ClassroomService(self.db)
            if classes.membership(user.id) and normalized_id not in classes.track_ids(user.id):
                return False
        # Email lists and legacy grants no longer gate competition access.
        # The original team competition still requires roster confirmation.
        if normalized_id not in {self.GLOBAL_GRANT, self.HACKATHON_ID}:
            return True
        if user is None:
            return False
        if normalized_id == self.GLOBAL_GRANT:
            return bool(self.db.scalar(
                select(CompetitionAccessGrant.id).where(
                    CompetitionAccessGrant.user_id == user.id,
                    CompetitionAccessGrant.competition_id == self.GLOBAL_GRANT,
                ).limit(1)
            ))
        return bool(self.db.scalar(
            select(CompetitionEntry.id)
            .join(TeamMembership, TeamMembership.team_id == CompetitionEntry.team_id)
            .where(
                CompetitionEntry.competition_id == normalized_id,
                CompetitionEntry.confirmed_at.is_not(None),
                TeamMembership.user_id == user.id,
            ).limit(1)
        ))

    def require_access(self, user: User | None, competition_id: str) -> None:
        normalized_id = competition_id.strip().lower()
        if user is not None and user.is_admin:
            return
        if self._competition_status(normalized_id) == "upcoming":
            raise PermissionError("This competition has not started yet")
        if user is None and self.is_hackathon_competition(competition_id):
            raise PermissionError("Sign in to confirm your team and access this official competition")
        if not self.can_access(user, competition_id):
            raise PermissionError("This track is not available to your class, or requires a confirmed competition team")

    def _competition_status(self, competition_id: str) -> str | None:
        # Import locally to keep catalog discovery independent from access
        # policy initialization.
        from app.services.requirement_catalog import RequirementCatalogService

        competition = next(
            (
                item
                for item in RequirementCatalogService.for_catalog(self.db, "competition").list_competitions()
                if item.id == competition_id
            ),
            None,
        )
        return str(competition.status or "").strip().lower() if competition else None

    def grant(self, *, user_id: str, competition_id: str, granted_by: str) -> CompetitionAccessGrant:
        normalized_id = competition_id.strip().lower()
        with hold_advisory_xact_lock(self.db, f"competition-access:{user_id}:{normalized_id}"):
            existing = self.db.scalar(
                select(CompetitionAccessGrant).where(
                    CompetitionAccessGrant.user_id == user_id,
                    CompetitionAccessGrant.competition_id == normalized_id,
                )
            )
            if existing:
                return existing
            grant = CompetitionAccessGrant(user_id=user_id, competition_id=normalized_id, granted_by=granted_by)
            self.db.add(grant)
            return grant
