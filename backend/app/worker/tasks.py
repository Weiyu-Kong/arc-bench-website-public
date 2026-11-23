"""Celery tasks for asynchronous ARC-Bench evaluation."""

from celery import Task
import threading
import time

from app.db.session import SessionLocal
from app.worker.celery_app import celery_app
from app.worker.run_claims import claim_run
from app.worker.run_claims import heartbeat_run
from app.worker.run_claims import finish_attempt
from app.services.executor_registry import ExecutorRegistry
from app.core.config import get_settings
from app.models.preview_instance import PreviewInstance
from app.services.preview_runtime import PreviewRuntime
from app.worker.preview_claims import begin_expiry, claim_preview, heartbeat_preview, update_owned
from datetime import datetime, timedelta
from pathlib import Path


class ArcBenchTask(Task):
    autoretry_for = (TimeoutError, ConnectionError)
    retry_backoff = True
    retry_backoff_max = 300
    retry_jitter = True
    max_retries = 2


class PreviewTask(ArcBenchTask):
    max_retries = None


class PreviewOwnershipLost(RuntimeError):
    def __init__(self, status: str | None) -> None:
        self.status = status
        super().__init__(f"Preview ownership was lost with status {status or 'missing'}")


@celery_app.task(bind=True, base=ArcBenchTask, name="arcbench.run_submission", acks_late=True)
def run_submission_task(
    self: Task,
    run_id: str,
    attempt_id: str,
    generation: int,
    reuse_workspace: bool = False,
) -> dict[str, str]:
    """Claim and execute one run. Duplicate Celery deliveries are harmless."""
    worker_id = str(self.request.hostname or "unknown-worker")
    task_id = str(self.request.id)
    db = SessionLocal()
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    # Celery normally uses prefork workers. Create a short-lived Redis client
    # in the child process instead of inheriting a client/thread from the
    # parent process.
    registry = ExecutorRegistry()
    task_registered = False
    completed_status = "failed"
    try:
        print(
            f"[executor] task_received task_id={task_id} run_id={run_id} attempt_id={attempt_id} generation={generation}",
            flush=True,
        )
        if not claim_run(db, run_id, attempt_id, generation, registry.executor_id, worker_id):
            print(f"[executor] task_not_claimed task_id={task_id} run_id={run_id}", flush=True)
            return {"run_id": run_id, "status": "already_claimed"}
        task_registered = True
        registry.task_started(task_id, run_id=run_id)
        print(
            f"[executor] task_claimed task_id={task_id} run_id={run_id} executor_id={registry.executor_id}",
            flush=True,
        )

        def refresh_lease() -> None:
            while not stop_heartbeat.wait(30):
                heartbeat_db = SessionLocal()
                try:
                    if not heartbeat_run(heartbeat_db, run_id, attempt_id, generation, worker_id):
                        stop_heartbeat.set()
                        return
                except Exception:
                    heartbeat_db.rollback()
                    stop_heartbeat.set()
                    return
                finally:
                    heartbeat_db.close()

        heartbeat_thread = threading.Thread(target=refresh_lease, name=f"lease-{run_id}", daemon=True)
        heartbeat_thread.start()

        # ExecutionService owns the detailed state transitions and artifact
        # handling. It creates its own short-lived session internally.
        from app.services.execution_service import ExecutionService

        if reuse_workspace:
            ExecutionService(db, run_id=run_id, attempt_id=attempt_id, generation=generation, ownership_lost=stop_heartbeat).rerun_submission(run_id)
        else:
            ExecutionService(db, run_id=run_id, attempt_id=attempt_id, generation=generation, ownership_lost=stop_heartbeat).run_submission(run_id)
        print(f"[executor] execution_returned task_id={task_id} run_id={run_id}", flush=True)
        finish_attempt(db, run_id, attempt_id, generation)
        completed_status = "completed"
        print(f"[executor] attempt_finished task_id={task_id} run_id={run_id}", flush=True)
        return {"run_id": run_id, "status": "completed"}
    except Exception as exc:
        db.rollback()
        print(
            f"[executor] task_failed task_id={task_id} run_id={run_id} error={type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        db.close()
        if task_registered:
            try:
                registry.task_finished(task_id, status=completed_status)
                print(f"[executor] task_registry_released task_id={task_id} run_id={run_id}", flush=True)
            except Exception as exc:
                print(
                    f"[executor] task_registry_release_failed task_id={task_id} run_id={run_id} "
                    f"error={type(exc).__name__}: {exc}",
                    flush=True,
                )
        registry.client.close()
        print(f"[executor] task_slot_releasing task_id={task_id} run_id={run_id}", flush=True)


@celery_app.task(bind=True, base=PreviewTask, name="arcbench.start_preview", acks_late=True)
def start_preview_task(self: Task, preview_id: str, generation: int) -> dict[str, str]:
    """Claim one preview generation and stay alive for its complete lifecycle."""
    settings = get_settings()
    worker_id = str(self.request.hostname or "unknown-previewer")
    previewer_id = worker_id.split("@", 1)[0].removeprefix("previewer-")
    db = SessionLocal()
    runtime: PreviewRuntime | None = None
    claimed = False
    task_id = str(self.request.id)
    terminal_status: str | None = None
    failure_reason: str | None = None

    def check_ownership() -> None:
        heartbeat_db = SessionLocal()
        try:
            status = heartbeat_preview(heartbeat_db, preview_id, generation, previewer_id, task_id)
        finally:
            heartbeat_db.close()
        if status not in {"STARTING", "READY"}:
            raise PreviewOwnershipLost(status)

    try:
        try:
            preview = claim_preview(db, preview_id, generation, previewer_id, worker_id, task_id)
        except RuntimeError as exc:
            if "no free configured ports" in str(exc):
                raise self.retry(exc=exc, countdown=10)
            raise
        if preview is None:
            return {"preview_id": preview_id, "status": "already_claimed"}
        claimed = True
        runtime = PreviewRuntime(
            Path(preview.workspace_path or ""), Path(preview.source_snapshot_path),
            preview.source_snapshot_sha256, settings.previewer_bind_host, int(preview.port or 0),
        )
        runtime.start(settings.preview_startup_timeout_seconds, check_ownership)
        now = datetime.utcnow()
        if not update_owned(
            db, preview_id, generation, previewer_id, task_id,
            allowed_statuses={"STARTING"},
            status="READY", ready_at=now, last_accessed_at=now,
            expires_at=now + timedelta(seconds=settings.preview_absolute_expiry_seconds),
        ):
            check_ownership()
        while runtime.running():
            time.sleep(settings.previewer_heartbeat_seconds)
            heartbeat_db = SessionLocal()
            try:
                status = heartbeat_preview(heartbeat_db, preview_id, generation, previewer_id, task_id)
                now = datetime.utcnow()
                if status == "READY" and begin_expiry(
                    heartbeat_db,
                    preview_id,
                    generation,
                    previewer_id,
                    task_id,
                    idle_cutoff=now - timedelta(seconds=settings.preview_idle_timeout_seconds),
                    now=now,
                ):
                    terminal_status = "EXPIRED"
                    break
                if status not in {"STARTING", "READY"}:
                    terminal_status = "STALE" if status == "STALE_REQUESTED" else "STOPPED" if status == "STOP_REQUESTED" else "LOST"
                    update_owned(
                        heartbeat_db,
                        preview_id,
                        generation,
                        previewer_id,
                        task_id,
                        allowed_statuses={status} if status else set(),
                        status="STOPPING",
                    )
                    break
            finally:
                heartbeat_db.close()
        else:
            terminal_status = "FAILED"
            failure_reason = "Preview backend exited"
            update_owned(
                db,
                preview_id,
                generation,
                previewer_id,
                task_id,
                allowed_statuses={"READY"},
                status="STOPPING",
            )
    except PreviewOwnershipLost as exc:
        terminal_status = "STALE" if exc.status == "STALE_REQUESTED" else "STOPPED" if exc.status == "STOP_REQUESTED" else "LOST"
        update_owned(
            db,
            preview_id,
            generation,
            previewer_id,
            task_id,
            allowed_statuses={exc.status} if exc.status else set(),
            status="STOPPING",
        )
    except Exception as exc:
        db.rollback()
        if not claimed:
            raise
        terminal_status = "FAILED"
        failure_reason = str(exc)[:2000]
        update_owned(
            db,
            preview_id,
            generation,
            previewer_id,
            task_id,
            allowed_statuses={"STARTING", "READY"},
            status="STOPPING",
        )
    finally:
        if runtime:
            runtime.cleanup()
        if terminal_status:
            update_owned(
                db,
                preview_id,
                generation,
                previewer_id,
                task_id,
                allowed_statuses={"STOPPING"},
                status=terminal_status,
                stopped_at=datetime.utcnow(),
                lease_until=None,
                failure_reason=failure_reason,
            )
        db.close()
    return {"preview_id": preview_id, "status": (terminal_status or "stopped").lower()}
