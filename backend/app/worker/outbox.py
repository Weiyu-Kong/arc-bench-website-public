"""Helpers for writing durable Celery messages in the same DB transaction."""

import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.run import Run
from app.models.submission import Submission
from app.models.run_attempt import RunAttempt
from app.models.task_outbox import TaskOutbox
from app.models.user import User
from app.services.personal_run_credentials import use_personal_run_credentials

ACTIVE_RUN_STATUSES = ("QUEUED", "STARTING", "RUNNING", "PAUSE_REQUESTED", "RESUME_REQUESTED")
MAX_GLOBAL_ACTIVE_RUNS = 400


class CompetitionRunAlreadyActive(ValueError):
    def __init__(self) -> None:
        super().__init__("Please stop the previous run for this task before trying again.")


class GlobalRunConcurrencyLimitExceeded(ValueError):
    def __init__(self, active: int) -> None:
        self.active = active
        super().__init__(f"The platform already has {active} active runs; maximum is {MAX_GLOBAL_ACTIVE_RUNS}")


def enqueue_attempt(db: Session, attempt: RunAttempt) -> TaskOutbox:
    row = TaskOutbox(
        id=uuid.uuid4().hex,
        run_id=attempt.run_id,
        task_name="arcbench.run_submission",
        payload_json=json.dumps(
            {
                "run_id": attempt.run_id,
                "attempt_id": attempt.id,
                "generation": attempt.generation,
                "reuse_workspace": attempt.reuse_workspace,
            }
        ),
    )
    db.add(row)
    return row


def create_attempt(db: Session, run: Run, *, reuse_workspace: bool, generation: int | None = None) -> TaskOutbox:
    generation = generation if generation is not None else run.active_generation + 1
    attempt = RunAttempt(
        id=uuid.uuid4().hex,
        run_id=run.id,
        generation=generation,
        status="QUEUED",
        reuse_workspace=reuse_workspace,
    )
    run.active_generation = generation
    run.active_attempt_id = attempt.id
    run.attempt_count = generation
    db.add(attempt)
    return enqueue_attempt(db, attempt)


def queue_run(db: Session, run_id: str, *, reuse_workspace: bool = False) -> TaskOutbox | None:
    """Atomically transition a Run to QUEUED and create its Outbox message.

    Returns the new row, or ``None`` when the run is already queued/running or
    terminal. The caller owns the surrounding transaction and must commit.
    """
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise LookupError(f"Run '{run_id}' not found")
    if run.status == "QUEUED":
        return None
    if run.status not in {"PENDING", "RUNNING", "RESUME_REQUESTED", "PAUSED"}:
        raise ValueError(f"Run '{run_id}' cannot be queued from status {run.status}")

    _enforce_competition_open(db, run)
    use_personal_run_credentials(run, db.get(Submission, run.submission_id) if run.billing_mode == "official_evaluation" else None)

    enforce_official_competition_task_limit(db, run, exclude_run_id=run.id)
    # Trusted host-side demos have their own lifecycle and do not consume the
    # platform's Docker evaluation capacity.
    if run.agent_source != "demo_replay":
        _enforce_global_limit(db, exclude_run_id=run.id)
    run.status = "QUEUED"
    run.worker_id = None
    run.lease_until = None
    run.heartbeat_at = None
    row = create_attempt(db, run, reuse_workspace=reuse_workspace)
    db.add(run)
    return row


def _enforce_competition_open(db: Session, run: Run) -> None:
    if run.catalog != "competition" or not run.competition_id:
        return
    from app.services.requirement_catalog import RequirementCatalogService

    competition = next(
        (
            item
            for item in RequirementCatalogService.for_catalog(db, "competition").list_competitions()
            if item.id == run.competition_id
        ),
        None,
    )
    if competition is None:
        raise ValueError("Competition is no longer available")
    status = str(competition.status or "").strip().lower()
    if status == "upcoming":
        raise ValueError("This competition has not started yet")
    if status != "open":
        raise ValueError("This competition is no longer accepting runs")


def _is_official_competition(db: Session, competition_id: str | None) -> bool:
    if not competition_id:
        return False
    # Competition metadata is filesystem-backed rather than stored on Run.
    # Resolve it at admission time so this rule only applies to formal events.
    from app.services.requirement_catalog import RequirementCatalogService

    competition = next(
        (
            item
            for item in RequirementCatalogService.for_catalog(db, "competition").list_competitions()
            if item.id == competition_id
        ),
        None,
    )
    return competition is not None and competition.type == "official"


def _lock_user(db: Session, user_id: str | None) -> str:
    if not user_id:
        raise ValueError("Run has no owner user")
    locked_user_id = db.execute(
        select(User.id).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if locked_user_id is None:
        raise ValueError("Run owner user does not exist")
    return user_id


def enforce_official_competition_task_limit(
    db: Session, run: Run, *, exclude_run_id: str | None = None
) -> None:
    """Enforce the (competition, task, user) active-run tuple for formal events."""
    if run.catalog != "competition" or not _is_official_competition(db, run.competition_id):
        return
    _enforce_competition_task_limit(db, run, exclude_run_id=exclude_run_id)


def _enforce_competition_task_limit(
    db: Session, run: Run, *, exclude_run_id: str | None = None
) -> None:
    """Allow one active official-competition container per user and task."""
    user_id = _lock_user(db, run.user_id)
    query = select(func.count()).select_from(Run).where(
        Run.user_id == user_id,
        Run.catalog == "competition",
        Run.competition_id == run.competition_id,
        Run.requirement_id == run.requirement_id,
        Run.status.in_(ACTIVE_RUN_STATUSES),
    )
    if exclude_run_id:
        query = query.where(Run.id != exclude_run_id)
    if (db.scalar(query) or 0) >= 1:
        raise CompetitionRunAlreadyActive()


def _enforce_global_limit(db: Session, *, exclude_run_id: str | None = None) -> None:
    """Reserve global capacity using a PostgreSQL transaction advisory lock."""
    db.execute(select(func.pg_advisory_xact_lock(804_271_004)))
    query = select(func.count()).select_from(Run).where(Run.status.in_(ACTIVE_RUN_STATUSES))
    if exclude_run_id:
        query = query.where(Run.id != exclude_run_id)
    active = db.scalar(query) or 0
    if active >= MAX_GLOBAL_ACTIVE_RUNS:
        raise GlobalRunConcurrencyLimitExceeded(int(active))
