"""Monitor expired run attempts and create fenced replacement generations."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.run import Run
from app.models.run_attempt import RunAttempt
from app.models.preview_instance import PreviewInstance
from app.services.preview_service import PreviewService
from app.services.official_evaluation_service import OfficialEvaluationService
from app.worker.outbox import create_attempt

LOG = logging.getLogger("arcbench.monitor")


def monitor_once(*, batch_size: int = 100) -> tuple[int, int]:
    """Fence expired active attempts; return (requeued, failed)."""
    settings = get_settings()
    db = SessionLocal()
    requeued = failed = 0
    try:
        now = datetime.utcnow()
        attempts = db.scalars(
            select(RunAttempt)
            .where(RunAttempt.status.in_(["STARTING", "RUNNING"]), RunAttempt.lease_until < now)
            .order_by(RunAttempt.lease_until)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
        previews = db.scalars(
            select(PreviewInstance)
            .where(
                PreviewInstance.status.in_(["STARTING", "READY", "STOP_REQUESTED", "STALE_REQUESTED", "STOPPING"]),
                PreviewInstance.lease_until < now,
            )
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
        for preview in previews:
            previous_status = preview.status
            run = db.scalar(select(Run).where(Run.id == preview.run_id).with_for_update())
            latest = db.scalar(
                select(PreviewInstance)
                .where(PreviewInstance.run_id == preview.run_id)
                .order_by(PreviewInstance.generation.desc())
                .limit(1)
            )
            # Keep the port quarantined until the owning node confirms that its
            # process group has stopped.
            preview.status = "CLEANUP_REQUIRED"
            preview.failure_reason = "Previewer lease expired"
            if (
                previous_status in {"STARTING", "READY"}
                and run is not None
                and latest is not None
                and latest.id == preview.id
            ):
                PreviewService(db).create_generation(
                    run,
                    generation=preview.generation + 1,
                    snapshot_path=preview.source_snapshot_path,
                    snapshot_sha256=preview.source_snapshot_sha256,
                    source_revision=preview.source_revision,
                )
        for attempt in attempts:
            run = db.scalar(select(Run).where(Run.id == attempt.run_id).with_for_update())
            if run is None or run.active_attempt_id != attempt.id or run.active_generation != attempt.generation:
                attempt.status = "LOST"
                attempt.finished_at = now
                attempt.failure_reason = "Lease expired after attempt was superseded"
                continue
            if run.status in {"PASSED", "FAILED", "CANCELLED", "PAUSED"}:
                attempt.status = "FAILED" if run.status == "FAILED" else "FINISHED"
                attempt.finished_at = now
                attempt.lease_until = None
                attempt.failure_reason = run.failure_reason if run.status == "FAILED" else None
                run.active_attempt_id = None
                run.worker_id = None
                run.celery_task_id = None
                run.lease_until = None
                run.heartbeat_at = None
                continue
            attempt.status = "LOST"
            attempt.finished_at = now
            attempt.failure_reason = "Worker lease expired"
            run.active_attempt_id = None
            run.active_generation = attempt.generation + 1
            run.worker_id = None
            run.celery_task_id = None
            run.lease_until = None
            run.heartbeat_at = None
            if run.status == "PAUSE_REQUESTED":
                # A remote monitor cannot prove that the executor-local process
                # has stopped, so do not expose its workspace for manual edits.
                run.status = "FAILED"
                run.failure_reason = "Worker lease expired while stopping for pause"
                run.finished_at = now
                failed += 1
                continue
            if attempt.generation >= settings.recovery_max_attempts:
                run.status = "FAILED"
                run.failure_reason = "Worker lease expired too many times"
                run.finished_at = now
                failed += 1
                continue
            run.status = "QUEUED"
            run.failure_reason = None
            create_attempt(db, run, reuse_workspace=False, generation=run.active_generation)
            requeued += 1
        db.commit()
        settled, settlement_failed = OfficialEvaluationService(db, settings).reconcile_terminal_runs(
            limit=batch_size
        )
        if settled or settlement_failed:
            LOG.info(
                "official evaluation reconciliation: settled=%s failed=%s",
                settled,
                settlement_failed,
            )
        return requeued, failed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def recover_once(*, batch_size: int = 100) -> tuple[int, int]:
    """Compatibility alias for existing deployments."""
    return monitor_once(batch_size=batch_size)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    LOG.info("ARC-Bench run attempt monitor started")
    while True:
        requeued, failed = monitor_once()
        if requeued or failed:
            LOG.info("attempt monitor: requeued=%s failed=%s", requeued, failed)
        time.sleep(settings.recovery_interval_seconds)


if __name__ == "__main__":
    main()
