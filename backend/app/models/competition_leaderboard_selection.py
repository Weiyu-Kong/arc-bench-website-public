from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CompetitionLeaderboardSelection(Base):
    """A participant's explicit snapshot preference within one competition."""

    __tablename__ = "competition_leaderboard_selections"

    competition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    participant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Keep stale pointers harmless: resolution falls back to the latest active
    # snapshot when the chosen submission has been deleted or deprecated.
    submission_id: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
