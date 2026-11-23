"""Freeze completed competitions without restricting other catalogs."""

from datetime import datetime, time, timedelta, timezone


def competition_has_ended(db, competition_id: str | None) -> bool:
    from app.services.requirement_catalog import RequirementCatalogService

    normalized = str(competition_id or "").strip().lower()
    if not normalized:
        return False
    competition = next((item for item in RequirementCatalogService.for_catalog(
        db, "competition"
    ).list_competitions() if item.id == normalized), None)
    if competition is None:
        return False
    if str(competition.status or "").lower() == "ended":
        return True
    value = str(competition.ends_at or "").strip()
    if not value:
        return False
    # Competition calendar dates include the whole final day in Beijing time.
    deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if len(value) == 10:
        deadline = datetime.combine(deadline.date(), time.max)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone(timedelta(hours=8)))
    return datetime.now(timezone.utc) > deadline
