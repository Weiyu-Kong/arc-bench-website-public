"""Database-side leasing for idempotent evaluation tasks."""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.run import Run
from app.models.run_attempt import RunAttempt


def claim_run(
    db: Session,
    run_id: str,
    attempt_id: str,
    generation: int,
    executor_id: str,
    worker_id: str,
    *,
    lease_seconds: int = 120,
) -> bool:
    """Atomically claim a queued run; return False for duplicate deliveries."""
    now = datetime.utcnow()
    result = db.execute(
        update(RunAttempt)
        .where(
            RunAttempt.id == attempt_id,
            RunAttempt.run_id == run_id,
            RunAttempt.generation == generation,
            RunAttempt.status == "QUEUED",
            RunAttempt.run_id.in_(
                select(Run.id).where(
                    Run.id == run_id,
                    Run.active_attempt_id == attempt_id,
                    Run.active_generation == generation,
                    Run.status == "QUEUED",
                )
            ),
        )
        .values(
            status="STARTING",
            executor_id=executor_id,
            worker_id=worker_id,
            lease_until=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            started_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    run_result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.active_attempt_id == attempt_id, Run.active_generation == generation)
        .values(
            status="STARTING",
            worker_id=worker_id,
            lease_until=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
        )
    )
    if run_result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def heartbeat_run(
    db: Session,
    run_id: str,
    attempt_id: str,
    generation: int,
    worker_id: str,
    *,
    lease_seconds: int = 120,
) -> bool:
    now = datetime.utcnow()
    result = db.execute(
        update(RunAttempt)
        .where(
            RunAttempt.id == attempt_id,
            RunAttempt.run_id == run_id,
            RunAttempt.generation == generation,
            RunAttempt.worker_id == worker_id,
            RunAttempt.status.in_(["STARTING", "RUNNING"]),
            RunAttempt.run_id.in_(
                select(Run.id).where(
                    Run.id == run_id,
                    Run.active_attempt_id == attempt_id,
                    Run.active_generation == generation,
                )
            ),
        )
        .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
    )
    if result.rowcount == 1:
        db.execute(
            update(Run)
            .where(Run.id == run_id, Run.active_attempt_id == attempt_id, Run.active_generation == generation)
            .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
        )
    db.commit()
    return result.rowcount == 1


def finish_attempt(db: Session, run_id: str, attempt_id: str, generation: int) -> bool:
    now = datetime.utcnow()
    run = db.scalar(
        select(Run).where(
            Run.id == run_id,
            Run.active_attempt_id == attempt_id,
            Run.active_generation == generation,
        )
    )
    if run is None:
        db.rollback()
        return False
    attempt_status = "FAILED" if run.status == "FAILED" else "FINISHED"
    result = db.execute(
        update(RunAttempt)
        .where(
            RunAttempt.id == attempt_id,
            RunAttempt.run_id == run_id,
            RunAttempt.generation == generation,
            RunAttempt.status.in_(["STARTING", "RUNNING"]),
        )
        .values(
            status=attempt_status,
            finished_at=now,
            lease_until=None,
            failure_reason=run.failure_reason if attempt_status == "FAILED" else None,
        )
    )
    if result.rowcount == 1:
        run.active_attempt_id = None
        run.worker_id = None
        run.lease_until = None
        run.heartbeat_at = None
    db.commit()
    return result.rowcount == 1
