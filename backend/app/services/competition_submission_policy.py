"""Deletion policy for competition agent snapshots."""

from app.models.submission import Submission
from app.services.competition_freeze_policy import competition_has_ended


def deletion_reason(db, submission: Submission, *, latest_id: str | None = None, runs=None) -> str | None:
    if submission.catalog != "competition":
        return None
    if competition_has_ended(db, submission.competition_id):
        return "This competition has ended and its submissions are read-only"
    return None
