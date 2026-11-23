"""Add optional real name and student ID to user profiles."""

from alembic import op
import sqlalchemy as sa


revision = "20260917_01"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("real_name", sa.String(120), nullable=True))
    op.add_column("users", sa.Column("student_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "student_id")
    op.drop_column("users", "real_name")
