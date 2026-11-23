from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RunTemplateArtifact(Base):
    __tablename__ = "run_template_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    competition_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requirement_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    competition_entry_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    archive_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class SubmissionTemplateSource(Base):
    __tablename__ = "submission_template_sources"
    __table_args__ = (UniqueConstraint("submission_id", "target_requirement_id", name="uq_submission_template_target"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    target_requirement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
