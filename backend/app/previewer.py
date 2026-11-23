from __future__ import annotations

import os
import shutil
import socket
import uuid
from pathlib import Path
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.preview_instance import PreviewInstance
from app.worker.celery_app import celery_app


def _identity() -> str:
    settings = get_settings()
    if settings.previewer_id:
        return _validate_identity(settings.previewer_id)
    path = settings.previewer_identity_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return _validate_identity(path.read_text(encoding="utf-8").strip())
    value = f"{socket.gethostname()}-{uuid.uuid4().hex}"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)
    return _validate_identity(value)


def _validate_identity(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200 or "@" in normalized:
        raise RuntimeError("Previewer identity must be 1-200 characters and must not contain '@'")
    return normalized


def main() -> None:
    settings = get_settings()
    identity = _identity()
    settings.previewer_workspace_root.mkdir(parents=True, exist_ok=True)
    # A node restart cannot retain child processes from its old container.
    # Clear only this node's local disposable preview workspaces before claims.
    for child in settings.previewer_workspace_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
    db = SessionLocal()
    try:
        stale_rows = db.scalars(select(PreviewInstance).where(
            PreviewInstance.previewer_id == identity,
            PreviewInstance.status.in_([
                "STARTING", "READY", "STOP_REQUESTED", "STALE_REQUESTED", "STOPPING", "CLEANUP_REQUIRED",
            ]),
        ).with_for_update()).all()
        now = datetime.utcnow()
        for row in stale_rows:
            row.status = "LOST"
            row.stopped_at = now
            row.lease_until = None
            row.port = None
            row.failure_reason = "Previewer restarted before preview cleanup completed"
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    celery_app.worker_main([
        "worker", "--loglevel=INFO", "--queues=preview.default",
        f"--concurrency={settings.previewer_concurrency}", f"--hostname=previewer-{identity}@%h",
    ])
