"""Shared visibility and ranking query for public and administrator leaderboards."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.requirement import CompetitionLeaderboardEntry
from app.services.requirement_catalog import RequirementCatalogService
from app.services.competition_access_service import CompetitionAccessService
from app.services.competition_standings_visibility_service import (
    CompetitionStandingsVisibilityService, PUBLIC_HACKATHON_LEADERBOARDS,
)


def query_competition_leaderboard(
    db: Session, current_user: User | None, *, track: str = "all",
    competition_id: str | None = None, task_id: str | None = None,
    funding_source: str = "all", classroom_id: str | None = None,
) -> list[CompetitionLeaderboardEntry]:
    normalized_id = competition_id.strip().lower() if competition_id else None
    if normalized_id and normalized_id not in PUBLIC_HACKATHON_LEADERBOARDS:
        try:
            CompetitionAccessService(db).require_access(current_user, normalized_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403 if current_user else 401, detail=str(exc)) from exc
    service = RequirementCatalogService.for_catalog(db, "competition")
    access = CompetitionAccessService(db)
    allowed_ids = {
        item.id
        for item in service.list_competitions()
        if access.is_listed_competition(item.id)
        and (item.id in PUBLIC_HACKATHON_LEADERBOARDS or access.can_access(current_user, item.id))
    }
    visibility = CompetitionStandingsVisibilityService(db)

    def public_round(round_id: str, selected_task: str | None):
        rows = service.list_competition_leaderboard(
            track, competition_id=round_id, task_id=selected_task,
            allowed_competition_ids=allowed_ids,
        )
        overall = None
        if round_id == "hackathon" and selected_task and selected_task.strip().lower() != "all":
            overall = service.list_competition_leaderboard(
                "all", competition_id=round_id, task_id="all", allowed_competition_ids=allowed_ids,
            )
        return visibility.filter(round_id, rows, preliminary_overall=overall)

    try:
        if normalized_id in PUBLIC_HACKATHON_LEADERBOARDS:
            entries = public_round(normalized_id, task_id)
        else:
            # The cross-competition view must not mix unfiltered round data
            # into legacy aggregates. Add each moderated round afterwards.
            entries = service.list_competition_leaderboard(
                track, competition_id=normalized_id, task_id=task_id,
                allowed_competition_ids=allowed_ids - PUBLIC_HACKATHON_LEADERBOARDS,
            )
            if normalized_id is None:
                for round_id in sorted(PUBLIC_HACKATHON_LEADERBOARDS & allowed_ids):
                    entries.extend(public_round(round_id, None))
                entries.sort(key=service._leaderboard_sort_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Competition standings are temporarily unavailable") from exc
    entries = visibility.filter_minimum_cost(entries)
    if funding_source != "all":
        entries = [entry for entry in entries if entry.funding_source == funding_source]
    if classroom_id:
        from app.services.classroom_service import ClassroomService
        try:
            entries = ClassroomService(db).filter_leaderboard(current_user.id if current_user else None, classroom_id, normalized_id, entries)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    for rank, entry in enumerate(entries, 1):
        entry.rank = rank
    return entries

