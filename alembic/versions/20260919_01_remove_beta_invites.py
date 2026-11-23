"""Remove internal beta invitation codes.

Existing beta users become standard users. Their existing competition access
grants are retained, but no longer identify an invitation-code source.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260919_01"
down_revision = "20260917_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET registration_source = 'standard' "
        "WHERE registration_source = 'beta'"
    )
    op.execute(
        "UPDATE competition_access_grants SET granted_by = 'legacy_access' "
        "WHERE granted_by = 'beta_invite'"
    )
    op.execute("DROP TABLE IF EXISTS beta_invite_codes")


def downgrade() -> None:
    # Redeemed codes cannot be reconstructed after upgrade, so this restores
    # only the legacy schema and leaves the table empty.
    op.create_table(
        "beta_invite_codes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("is_used", sa.Boolean(), nullable=False),
        sa.Column("redeemed_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["redeemed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(
        op.f("ix_beta_invite_codes_is_used"),
        "beta_invite_codes",
        ["is_used"],
        unique=False,
    )
