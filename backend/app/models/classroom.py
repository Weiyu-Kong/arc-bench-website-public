"""Server-owned classes and competition cohorts, independent of account type."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Classroom(Base):
    __tablename__ = "classrooms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class ClassroomTrack(Base):
    __tablename__ = "classroom_tracks"
    __table_args__ = (UniqueConstraint("classroom_id", "competition_id", name="uq_classroom_track"),)

    classroom_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id"), primary_key=True)
    competition_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class ClassroomMembership(Base):
    __tablename__ = "classroom_memberships"

    # One user can belong to at most one class or competition cohort.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    classroom_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id"), nullable=False, index=True)
    roster_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
