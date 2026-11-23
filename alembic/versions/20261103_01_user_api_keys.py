"""Add externally assigned per-user API keys."""
from alembic import op
import sqlalchemy as sa

revision = "20261103_01"
down_revision = "20261101_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.CheckConstraint(
            "char_length(api_key) BETWEEN 1 AND 4096 AND api_key !~ '^[[:space:]]|[[:space:]]$'",
            name="ck_user_api_keys_value",
        ),
    )
    op.create_index("ix_user_api_keys_user_id", "user_api_keys", ["user_id"])


def downgrade():
    op.drop_table("user_api_keys")
