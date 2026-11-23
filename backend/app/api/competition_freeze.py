from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.run import Run
from app.models.submission import Submission
from app.services.competition_freeze_policy import competition_has_ended


async def enforce_competition_freeze(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    """Reject writes only when their competition has ended."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return

    parts = request.url.path.rstrip("/").split("/")
    competition_ids = set()
    if "competitions" in parts:
        competition_ids.add(request.path_params.get("competition_id"))
    model = Run if "runs" in parts else Submission
    record_ids = []
    record_id = request.path_params.get("submission_id")
    if record_id:
        record_ids = [record_id]
    elif parts[-1] == "batch-delete":
        payload = await request.json()
        if isinstance(payload, dict) and isinstance(payload.get("ids"), list):
            record_ids = [item for item in payload["ids"] if isinstance(item, str)]
    elif parts[-1] in {"submissions", "runs"}:
        form = await request.form()
        if parts[-1] == "submissions":
            if str(form.get("catalog", "playground")).strip().lower() == "competition":
                competition_ids.add(form.get("competition_id"))
        else:
            model = Submission
            submission_id = form.get("submission_id")
            if isinstance(submission_id, str):
                record_ids = [submission_id]

    if record_ids:
        competition_ids.update(db.scalars(
            select(model.competition_id)
            .where(
                model.id.in_(record_ids),
                func.lower(func.trim(func.coalesce(model.catalog, ""))) == "competition",
            )
        ).all())
    if any(competition_has_ended(db, competition_id) for competition_id in competition_ids):
        raise HTTPException(
            status_code=403,
            detail="This competition has ended and is read-only",
        )
