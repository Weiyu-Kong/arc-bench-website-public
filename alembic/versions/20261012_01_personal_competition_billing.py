"""Allow budget settlement for individual competition entries."""

from alembic import op
import sqlalchemy as sa

revision = "20261012_01"
down_revision = "20261002_01"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("competition_budget_transactions", "team_id",
                    existing_type=sa.String(32), nullable=True)


def downgrade():
    op.alter_column("competition_budget_transactions", "team_id",
                    existing_type=sa.String(32), nullable=False)
