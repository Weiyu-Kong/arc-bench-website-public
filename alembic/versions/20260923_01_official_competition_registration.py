"""Add official competition confirmation and per-account budgets."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_01"
down_revision = "20260919_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "competition_entries",
        sa.Column("initial_budget_cny", sa.Numeric(precision=12, scale=2), nullable=False, server_default="500.00"),
    )
    op.add_column("competition_entries", sa.Column("confirmed_by_user_id", sa.String(length=64), nullable=True))
    op.add_column("competition_entries", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_competition_entries_confirmed_by_user_id"),
        "competition_entries",
        ["confirmed_by_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_competition_entries_confirmed_by_user_id_users",
        "competition_entries",
        "users",
        ["confirmed_by_user_id"],
        ["id"],
    )
    op.create_table(
        "competition_budgets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("initial_amount_cny", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("remaining_amount_cny", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competition_id", "user_id", name="uq_competition_budget_user"),
    )
    op.create_index(op.f("ix_competition_budgets_competition_id"), "competition_budgets", ["competition_id"], unique=False)
    op.create_index(op.f("ix_competition_budgets_user_id"), "competition_budgets", ["user_id"], unique=False)
    op.alter_column("competition_entries", "initial_budget_cny", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_competition_budgets_user_id"), table_name="competition_budgets")
    op.drop_index(op.f("ix_competition_budgets_competition_id"), table_name="competition_budgets")
    op.drop_table("competition_budgets")
    op.drop_constraint("fk_competition_entries_confirmed_by_user_id_users", "competition_entries", type_="foreignkey")
    op.drop_index(op.f("ix_competition_entries_confirmed_by_user_id"), table_name="competition_entries")
    op.drop_column("competition_entries", "confirmed_at")
    op.drop_column("competition_entries", "confirmed_by_user_id")
    op.drop_column("competition_entries", "initial_budget_cny")
