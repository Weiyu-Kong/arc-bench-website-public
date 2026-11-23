"""Store submission-owned model endpoint credentials."""

from alembic import op
import sqlalchemy as sa


revision = "20260909_03"
down_revision = "20260909_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("openai_base_url", sa.String(1024), nullable=True))
    op.add_column("submissions", sa.Column("openai_api_key", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("openai_base_url", sa.String(1024), nullable=True))
    op.add_column("runs", sa.Column("openai_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "openai_api_key")
    op.drop_column("runs", "openai_base_url")
    op.drop_column("submissions", "openai_api_key")
    op.drop_column("submissions", "openai_base_url")
