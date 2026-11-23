"""Revoke existing login sessions when a password is reset."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_02"
down_revision = "20261020_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("users", "session_version")
