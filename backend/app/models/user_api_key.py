import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserApiKey(Base):
    """An externally assigned credential, independent of submissions and teams."""

    __tablename__ = "user_api_keys"
    __table_args__ = (
        CheckConstraint(
            "char_length(api_key) BETWEEN 1 AND 4096 AND api_key !~ '^[[:space:]]|[[:space:]]$'",
            name="ck_user_api_keys_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("timezone('utc', now())"),
    )
