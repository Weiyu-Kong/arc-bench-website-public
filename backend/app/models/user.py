from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    session_version: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    # Account classification retained independently of the unified registration flow.
    registration_source: Mapped[str] = mapped_column(String(32), nullable=False, default="standard", index=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    @property
    def is_admin(self) -> bool:
        from app.core.config import get_settings
        return self.email.strip().casefold() in set(get_settings().admin_emails)
