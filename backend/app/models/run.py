from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Run(Base):
    """One execution of an agent Submission against a concrete task."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # No foreign key by design: deleting a competition submission must retain
    # the historical runs that were created from it.
    submission_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    submission_display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    visual_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    openai_base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    openai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Copied from the source snapshot so an historical run remains fully
    # navigable even after that reusable submission is deleted.
    catalog: Mapped[str] = mapped_column(String(32), nullable=False, default="playground", index=True)
    competition_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    competition_entry_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    competition_owner_display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    billing_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="self_funded", index=True)
    budget_competition_entry_id: Mapped[str | None] = mapped_column(ForeignKey("competition_entries.id"), nullable=True, index=True)
    budget_charge_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    meter_key_name: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    meter_account_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    meter_key_status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    evaluation_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settlement_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirement_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_source: Mapped[str] = mapped_column(String(64), nullable=False, default="upload", index=True)
    agent_archive_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_cost_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cost_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feature_implemented_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_implementation_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stdout_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stderr_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    manual_edit_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    template_source_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    template_archive_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template_archive_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_runs_status_lease_until", "status", "lease_until"),)
