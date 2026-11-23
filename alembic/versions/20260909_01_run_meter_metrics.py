"""Store token and currency metrics captured from ARC Bench Meter."""

from alembic import op
import sqlalchemy as sa


revision = "20260909_01"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("token_count", sa.BigInteger(), nullable=True))
    op.add_column("runs", sa.Column("token_cost_currency", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "token_cost_currency")
    op.drop_column("runs", "token_count")
