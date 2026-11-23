"""Per-attempt proxy credentials and immutable per-call metering snapshots."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ModelProxySession(Base):
    __tablename__ = "model_proxy_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ModelUsageRequest(Base):
    __tablename__ = "model_usage_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_host: Mapped[str] = mapped_column(String(255), nullable=False)
    upstream_request_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reported_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    price_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prices_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_model_usage_run_started", "run_id", "started_at"),)
