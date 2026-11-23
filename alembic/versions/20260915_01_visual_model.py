"""Store the user-selected visual model."""
from alembic import op
import sqlalchemy as sa

revision = "20260915_01"
down_revision = "20260909_03"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("submissions", sa.Column("visual_model", sa.String(120), nullable=True))
    op.add_column("runs", sa.Column("visual_model", sa.String(120), nullable=True))

def downgrade() -> None:
    op.drop_column("runs", "visual_model")
    op.drop_column("submissions", "visual_model")
