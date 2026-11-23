"""Persist participant choices of competition leaderboard submissions."""

from alembic import op
import sqlalchemy as sa

revision = "20261016_01"
down_revision = "20261012_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competition_leaderboard_selections",
        sa.Column("competition_id", sa.String(64), nullable=False),
        sa.Column("participant_id", sa.String(64), nullable=False),
        sa.Column("submission_id", sa.String(64), nullable=False),
        sa.Column("selected_by_user_id", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("competition_id", "participant_id"),
    )


def downgrade() -> None:
    op.drop_table("competition_leaderboard_selections")
