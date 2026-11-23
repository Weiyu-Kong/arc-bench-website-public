from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PreviewInstance(Base):
    """A generation-fenced preview process hosted by a previewer node."""

    __tablename__ = "preview_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    previewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_snapshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_preview_instance_run_generation"),
        Index("ix_preview_instances_status_lease", "status", "lease_until"),
        Index(
            "uq_preview_instances_active_node_port",
            "previewer_id",
            "port",
            unique=True,
            postgresql_where=text(
                "status IN ('STARTING', 'READY', 'STOP_REQUESTED', 'STALE_REQUESTED', 'STOPPING', 'CLEANUP_REQUIRED') "
                "AND previewer_id IS NOT NULL AND port IS NOT NULL"
            ),
        ),
    )
