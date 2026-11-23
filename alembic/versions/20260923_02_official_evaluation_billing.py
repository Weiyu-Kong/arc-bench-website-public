"""Add team-scoped Official Hackathon evaluation billing."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_02"
down_revision = "20260923_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competition_budgets_v2",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("competition_entry_id", sa.String(length=32), nullable=False),
        sa.Column("initial_amount_cny", sa.Numeric(12, 6), nullable=False),
        sa.Column("remaining_amount_cny", sa.Numeric(12, 6), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["competition_entry_id"], ["competition_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competition_entry_id", name="uq_competition_budget_entry"),
    )
    op.execute(
        """
        INSERT INTO competition_budgets_v2
            (id, competition_id, competition_entry_id, initial_amount_cny,
             remaining_amount_cny, granted_at, updated_at)
        SELECT md5(ce.id || '|team-budget'), ce.competition_id, ce.id,
               ce.initial_budget_cny,
               COALESCE(MIN(cb.remaining_amount_cny), ce.initial_budget_cny),
               COALESCE(MIN(cb.granted_at), CURRENT_TIMESTAMP),
               COALESCE(MAX(cb.updated_at), CURRENT_TIMESTAMP)
        FROM competition_entries ce
        LEFT JOIN team_memberships tm ON tm.team_id = ce.team_id
        LEFT JOIN competition_budgets cb
          ON cb.competition_id = ce.competition_id AND cb.user_id = tm.user_id
        WHERE ce.owner_kind = 'team' AND ce.confirmed_at IS NOT NULL
        GROUP BY ce.id, ce.competition_id, ce.initial_budget_cny
        """
    )
    op.drop_table("competition_budgets")
    op.rename_table("competition_budgets_v2", "competition_budgets")
    op.create_index("ix_competition_budgets_competition_id", "competition_budgets", ["competition_id"])
    op.create_index("ix_competition_budgets_competition_entry_id", "competition_budgets", ["competition_entry_id"])

    op.create_table(
        "competition_budget_transactions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("competition_budget_id", sa.String(length=32), nullable=False),
        sa.Column("competition_entry_id", sa.String(length=32), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("initiated_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("transaction_type", sa.String(length=24), nullable=False),
        sa.Column("amount_cny", sa.Numeric(12, 6), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["competition_budget_id"], ["competition_budgets.id"]),
        sa.ForeignKeyConstraint(["competition_entry_id"], ["competition_entries.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["initiated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("competition_budget_id", "competition_entry_id", "team_id", "run_id", "initiated_by_user_id", "transaction_type", "idempotency_key"):
        op.create_index(f"ix_competition_budget_transactions_{column}", "competition_budget_transactions", [column])

    op.add_column("submissions", sa.Column("credential_mode", sa.String(length=32), nullable=False, server_default="self_funded"))
    op.add_column("submissions", sa.Column("official_evaluation_started_at", sa.DateTime(), nullable=True))
    op.create_index("ix_submissions_credential_mode", "submissions", ["credential_mode"])
    op.alter_column("submissions", "credential_mode", server_default=None)

    run_columns = (
        sa.Column("billing_mode", sa.String(length=32), nullable=False, server_default="self_funded"),
        sa.Column("budget_competition_entry_id", sa.String(length=32), nullable=True),
        sa.Column("budget_charge_cny", sa.Numeric(12, 6), nullable=True),
        sa.Column("initiated_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("meter_key_name", sa.String(length=160), nullable=True),
        sa.Column("meter_account_id", sa.String(length=200), nullable=True),
        sa.Column("meter_key_status", sa.String(length=24), nullable=True),
        sa.Column("evaluation_started_at", sa.DateTime(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("settlement_error", sa.Text(), nullable=True),
    )
    for column in run_columns:
        op.add_column("runs", column)
    for column in ("billing_mode", "budget_competition_entry_id", "initiated_by_user_id", "meter_key_status"):
        op.create_index(f"ix_runs_{column}", "runs", [column])
    op.create_unique_constraint("uq_runs_meter_key_name", "runs", ["meter_key_name"])
    op.create_unique_constraint("uq_runs_meter_account_id", "runs", ["meter_account_id"])
    op.create_foreign_key(
        "fk_runs_budget_competition_entry_id",
        "runs",
        "competition_entries",
        ["budget_competition_entry_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_runs_initiated_by_user_id",
        "runs",
        "users",
        ["initiated_by_user_id"],
        ["id"],
    )
    op.alter_column("runs", "billing_mode", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_runs_initiated_by_user_id", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_budget_competition_entry_id", "runs", type_="foreignkey")
    op.drop_constraint("uq_runs_meter_account_id", "runs", type_="unique")
    op.drop_constraint("uq_runs_meter_key_name", "runs", type_="unique")
    for column in ("meter_key_status", "initiated_by_user_id", "budget_competition_entry_id", "billing_mode"):
        op.drop_index(f"ix_runs_{column}", table_name="runs")
    for column in ("settlement_error", "settled_at", "evaluation_started_at", "meter_key_status", "meter_account_id", "meter_key_name", "initiated_by_user_id", "budget_charge_cny", "budget_competition_entry_id", "billing_mode"):
        op.drop_column("runs", column)
    op.drop_index("ix_submissions_credential_mode", table_name="submissions")
    op.drop_column("submissions", "official_evaluation_started_at")
    op.drop_column("submissions", "credential_mode")
    op.drop_table("competition_budget_transactions")
    op.create_table(
        "competition_budgets_v1",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("competition_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("initial_amount_cny", sa.Numeric(12, 2), nullable=False),
        sa.Column("remaining_amount_cny", sa.Numeric(12, 2), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("competition_id", "user_id", name="uq_competition_budget_user"),
    )
    op.execute(
        """
        INSERT INTO competition_budgets_v1
            (id, competition_id, user_id, initial_amount_cny,
             remaining_amount_cny, granted_at, updated_at)
        SELECT md5(cb.competition_entry_id || '|' || tm.user_id), cb.competition_id,
               tm.user_id, cb.initial_amount_cny, cb.remaining_amount_cny,
               cb.granted_at, cb.updated_at
        FROM competition_budgets cb
        JOIN competition_entries ce ON ce.id = cb.competition_entry_id
        JOIN team_memberships tm ON tm.team_id = ce.team_id
        """
    )
    op.drop_table("competition_budgets")
    op.rename_table("competition_budgets_v1", "competition_budgets")
    op.create_index("ix_competition_budgets_competition_id", "competition_budgets", ["competition_id"])
    op.create_index("ix_competition_budgets_user_id", "competition_budgets", ["user_id"])
