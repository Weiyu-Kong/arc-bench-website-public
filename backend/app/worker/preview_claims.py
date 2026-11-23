from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.preview_instance import PreviewInstance


def claim_preview(db: Session, preview_id: str, generation: int, previewer_id: str, worker_id: str, task_id: str) -> PreviewInstance | None:
    settings = get_settings()
    row = db.scalar(select(PreviewInstance).where(
        PreviewInstance.id == preview_id,
        PreviewInstance.generation == generation,
        PreviewInstance.status == "REQUESTED",
    ).with_for_update())
    if row is None:
        db.rollback()
        return None
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"arcbench-previewer:{previewer_id}"))))
    used_ports = set(db.scalars(select(PreviewInstance.port).where(
        PreviewInstance.previewer_id == previewer_id,
        PreviewInstance.status.in_(["STARTING", "READY", "STOP_REQUESTED", "STALE_REQUESTED", "STOPPING", "CLEANUP_REQUIRED"]),
        PreviewInstance.port.is_not(None),
    )).all())
    port = next((candidate for candidate in range(settings.previewer_port_start, settings.previewer_port_end + 1) if candidate not in used_ports), None)
    if port is None:
        db.rollback()
        raise RuntimeError("Previewer has no free configured ports")
    now = datetime.utcnow()
    row.status = "STARTING"
    row.previewer_id = previewer_id
    row.worker_id = worker_id
    row.celery_task_id = task_id
    row.host = settings.previewer_public_host
    row.port = port
    row.started_at = now
    row.heartbeat_at = now
    row.lease_until = now + timedelta(seconds=settings.preview_startup_timeout_seconds + settings.previewer_lease_seconds)
    row.workspace_path = str(settings.previewer_workspace_root / row.id / str(generation))
    db.commit()
    db.refresh(row)
    return row


def update_owned(
    db: Session,
    preview_id: str,
    generation: int,
    previewer_id: str,
    task_id: str,
    *,
    allowed_statuses: set[str] | None = None,
    **values: object,
) -> bool:
    if allowed_statuses is not None and not allowed_statuses:
        db.rollback()
        return False
    query = db.query(PreviewInstance).filter(
        PreviewInstance.id == preview_id,
        PreviewInstance.generation == generation,
        PreviewInstance.previewer_id == previewer_id,
        PreviewInstance.celery_task_id == task_id,
    )
    if allowed_statuses is not None:
        query = query.filter(PreviewInstance.status.in_(allowed_statuses))
    updated = query.update(values, synchronize_session=False)
    db.commit()
    return updated == 1


def heartbeat_preview(db: Session, preview_id: str, generation: int, previewer_id: str, task_id: str) -> str | None:
    settings = get_settings()
    now = datetime.utcnow()
    updated = db.query(PreviewInstance).filter(
        PreviewInstance.id == preview_id,
        PreviewInstance.generation == generation,
        PreviewInstance.previewer_id == previewer_id,
        PreviewInstance.celery_task_id == task_id,
        PreviewInstance.status.in_(["STARTING", "READY"]),
    ).update({
        PreviewInstance.heartbeat_at: now,
        PreviewInstance.lease_until: now + timedelta(seconds=settings.previewer_lease_seconds),
    }, synchronize_session=False)
    db.commit()
    if updated:
        return db.scalar(select(PreviewInstance.status).where(PreviewInstance.id == preview_id))
    return db.scalar(select(PreviewInstance.status).where(
        PreviewInstance.id == preview_id,
        PreviewInstance.generation == generation,
        PreviewInstance.previewer_id == previewer_id,
        PreviewInstance.celery_task_id == task_id,
    ))


def begin_expiry(
    db: Session,
    preview_id: str,
    generation: int,
    previewer_id: str,
    task_id: str,
    *,
    idle_cutoff: datetime,
    now: datetime,
) -> bool:
    updated = db.query(PreviewInstance).filter(
        PreviewInstance.id == preview_id,
        PreviewInstance.generation == generation,
        PreviewInstance.previewer_id == previewer_id,
        PreviewInstance.celery_task_id == task_id,
        PreviewInstance.status == "READY",
        or_(
            PreviewInstance.last_accessed_at < idle_cutoff,
            PreviewInstance.expires_at < now,
        ),
    ).update({PreviewInstance.status: "STOPPING"}, synchronize_session=False)
    db.commit()
    return updated == 1
