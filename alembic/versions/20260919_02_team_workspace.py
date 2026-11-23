"""Add team slogans and navigable team notifications."""

from alembic import op
import sqlalchemy as sa


revision = "20260919_02"
down_revision = "20260919_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("slogan", sa.String(length=240), nullable=True))
    op.add_column(
        "notifications",
        sa.Column("team_join_request_id", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_notifications_team_join_request_id"),
        "notifications",
        ["team_join_request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_team_join_request_id"), table_name="notifications")
    op.drop_column("notifications", "team_join_request_id")
    op.drop_column("teams", "slogan")
