"""Preserve deprecated submission snapshots and their artifacts."""

from alembic import op
import sqlalchemy as sa

revision = "20261002_01"
down_revision = "20260923_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("submissions", sa.Column("deprecated_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("submissions", "deprecated_at")
