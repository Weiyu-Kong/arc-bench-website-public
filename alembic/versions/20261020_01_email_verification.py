"""Persist email verification without trusting previously registered addresses."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_01"
down_revision = "20261019_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "email_verified_at")
