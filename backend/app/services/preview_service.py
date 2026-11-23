from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.preview_instance import PreviewInstance
from app.models.run import Run
from app.models.task_outbox import TaskOutbox
from app.services.preview_source_service import PreviewSourceService


ACTIVE_STATUSES = {"REQUESTED", "STARTING", "READY", "STOP_REQUESTED", "STALE_REQUESTED", "STOPPING", "CLEANUP_REQUIRED"}
TERMINAL_STATUSES = {"STOPPED", "FAILED", "LOST", "EXPIRED", "STALE"}


class PreviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def latest(self, run_id: str) -> PreviewInstance | None:
        return self.db.scalar(select(PreviewInstance).where(PreviewInstance.run_id == run_id).order_by(PreviewInstance.generation.desc()).limit(1))

    def request(self, run: Run, source_template: Path | None = None) -> PreviewInstance:
        locked_run = self.db.scalar(select(Run).where(Run.id == run.id).with_for_update())
        if locked_run is None or not locked_run.user_id:
            raise LookupError(f"Run '{run.id}' not found")
        if source_template is not None:
            snapshot, digest, revision = PreviewSourceService().publish(locked_run.user_id, locked_run.id, source_template)
        else:
            snapshot, digest, revision = PreviewSourceService().current(locked_run.user_id, locked_run.id)
        previous = self.latest(locked_run.id)
        generation = (previous.generation if previous else 0) + 1
        if previous and previous.status in ACTIVE_STATUSES:
            if previous.status == "REQUESTED":
                previous.status = "STALE"
                previous.stopped_at = datetime.utcnow()
            else:
                previous.status = "STALE_REQUESTED"
        preview = self.create_generation(
            locked_run,
            generation=generation,
            snapshot_path=str(snapshot),
            snapshot_sha256=digest,
            source_revision=revision,
        )
        return preview

    def publish_source(self, run: Run, source_template: Path) -> tuple[Path, str, str | None]:
        if not run.user_id:
            raise ValueError("Run has no owner user")
        source_service = PreviewSourceService()
        published = source_service.publish(run.user_id, run.id, source_template)
        protected_paths = {
            Path(path).resolve()
            for path in self.db.scalars(
                select(PreviewInstance.source_snapshot_path).where(
                    PreviewInstance.run_id == run.id,
                    PreviewInstance.status.in_(ACTIVE_STATUSES),
                )
            ).all()
        }
        source_service.prune(run.user_id, run.id, protected_paths=protected_paths)
        return published

    def create_generation(
        self,
        run: Run,
        *,
        generation: int,
        snapshot_path: str,
        snapshot_sha256: str,
        source_revision: str | None,
    ) -> PreviewInstance:
        if not run.user_id:
            raise ValueError("Run has no owner user")
        preview = PreviewInstance(
            id=uuid.uuid4().hex,
            run_id=run.id,
            user_id=run.user_id,
            generation=generation,
            status="REQUESTED",
            source_revision=source_revision,
            source_snapshot_path=snapshot_path,
            source_snapshot_sha256=snapshot_sha256,
        )
        self.db.add(preview)
        self.db.flush()
        self.db.add(TaskOutbox(
            id=uuid.uuid4().hex,
            run_id=run.id,
            task_name="arcbench.start_preview",
            payload_json=json.dumps({"preview_id": preview.id, "generation": generation}),
        ))
        return preview

    def stop(self, run_id: str) -> PreviewInstance | None:
        self.db.execute(select(Run.id).where(Run.id == run_id).with_for_update()).scalar_one_or_none()
        preview = self.latest(run_id)
        if preview and preview.status in ACTIVE_STATUSES:
            if preview.status == "REQUESTED":
                preview.status = "STOPPED"
                preview.stopped_at = datetime.utcnow()
            else:
                preview.status = "STOP_REQUESTED"
        return preview

    def mark_stale(self, run_id: str, source_template: Path | None = None) -> PreviewInstance | None:
        run = self.db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            return None
        latest = self.latest(run_id)
        if source_template is not None and run.user_id:
            self.publish_source(run, source_template)
        if latest and latest.status in ACTIVE_STATUSES:
            if latest.status == "REQUESTED":
                latest.status = "STALE"
                latest.stopped_at = datetime.utcnow()
            else:
                latest.status = "STALE_REQUESTED"
        return latest

    def touch(self, preview: PreviewInstance) -> None:
        if preview.status != "READY":
            return
        touched_at = datetime.utcnow()
        updated = self.db.query(PreviewInstance).filter(
            PreviewInstance.id == preview.id,
            PreviewInstance.generation == preview.generation,
            PreviewInstance.status == "READY",
        ).update({PreviewInstance.last_accessed_at: touched_at}, synchronize_session=False)
        if updated:
            preview.last_accessed_at = touched_at

    def status_payload(self, run_id: str) -> dict[str, object]:
        preview = self.latest(run_id)
        if preview is None:
            return {"available": False, "stale": False, "status": None, "preview_url": None, "error": "Preview has not been requested"}
        return {
            "available": preview.status == "READY",
            "stale": preview.status == "STALE",
            "status": preview.status,
            "preview_id": preview.id,
            "generation": preview.generation,
            "preview_url": self.public_url(preview),
            "workspace_head_oid": preview.source_revision,
            "preview_head_oid": preview.source_revision,
            "requested_at": preview.requested_at,
            "ready_at": preview.ready_at,
            "expires_at": preview.expires_at,
            "error": preview.failure_reason,
        }

    def public_url(self, preview: PreviewInstance) -> str | None:
        gateway = (self.settings.preview_gateway_base_url or "").rstrip("/")
        if gateway:
            return f"{gateway}/{preview.id}"
        if preview.host and preview.port:
            base = (self.settings.previewer_public_base_url or "").rstrip("/")
            return f"{base}:{preview.port}" if base else f"http://{preview.host}:{preview.port}"
        return None
