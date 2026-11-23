"""Small durable Outbox dispatcher that publishes rows to Celery."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.task_outbox import TaskOutbox
from app.models.run import Run
from app.models.run_attempt import RunAttempt
from app.models.preview_instance import PreviewInstance
from app.worker.celery_app import celery_app

LOG = logging.getLogger("arcbench.dispatcher")


def dispatch_once(batch_size: int = 100) -> int:
    """Publish one batch; rows remain pending if publish or DB update fails."""
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(TaskOutbox)
            .where(TaskOutbox.sent_at.is_(None))
            .order_by(TaskOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        ).all()
        published = 0
        for row in rows:
            try:
                payload = json.loads(row.payload_json)
                queue = {
                    "arcbench.run_submission": "evaluation.default",
                    "arcbench.start_preview": "preview.default",
                }.get(row.task_name)
                if queue is None:
                    raise ValueError(f"Unsupported outbox task: {row.task_name}")
                result = celery_app.send_task(
                    row.task_name,
                    kwargs=payload,
                    task_id=f"arcbench-{row.id}",
                    queue=queue,
                )
                row.sent_at = datetime.utcnow()
                row.attempt_count += 1
                row.last_error = None
                # Keep the Celery id useful for support and cancellation.
                task_id = getattr(result, "id", None)
                if task_id and row.task_name == "arcbench.run_submission":
                    updated = db.query(RunAttempt).filter(
                        RunAttempt.id == payload.get("attempt_id"),
                        RunAttempt.run_id == row.run_id,
                        RunAttempt.generation == payload.get("generation"),
                    ).update({RunAttempt.celery_task_id: task_id})
                    if updated:
                        db.query(Run).filter(
                            Run.id == row.run_id,
                            Run.active_attempt_id == payload.get("attempt_id"),
                            Run.active_generation == payload.get("generation"),
                        ).update({Run.celery_task_id: task_id})
                elif task_id and row.task_name == "arcbench.start_preview":
                    db.query(PreviewInstance).filter(
                        PreviewInstance.id == payload.get("preview_id"),
                        PreviewInstance.generation == payload.get("generation"),
                        PreviewInstance.status == "REQUESTED",
                        PreviewInstance.celery_task_id.is_(None),
                    ).update({PreviewInstance.celery_task_id: task_id})
                if task_id:
                    LOG.info("dispatched outbox=%s task=%s queue=%s", row.id, task_id, queue)
                published += 1
            except Exception as exc:  # noqa: BLE001
                row.attempt_count += 1
                row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                LOG.exception("failed to dispatch outbox=%s", row.id)
        db.commit()
        return published
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    interval = 2.0
    LOG.info("ARC-Bench Outbox dispatcher started")
    while True:
        dispatch_once()
        time.sleep(interval)


if __name__ == "__main__":
    main()
