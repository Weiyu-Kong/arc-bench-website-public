"""Integrated model proxy credentials and per-request usage ledger."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_01"
down_revision = "20261016_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("cost_source", sa.String(32), nullable=True))
    op.add_column("runs", sa.Column("cost_complete", sa.Boolean(), nullable=True))
    op.create_table(
        "model_proxy_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_model_proxy_sessions_run_id", "model_proxy_sessions", ["run_id"])
    op.create_table(
        "model_usage_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=True),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("endpoint", sa.String(64), nullable=False),
        sa.Column("upstream_host", sa.String(255), nullable=False),
        sa.Column("upstream_request_id", sa.String(256), nullable=True),
        sa.Column("reported_model", sa.String(256), nullable=True),
        sa.Column("price_version", sa.String(120), nullable=False),
        sa.Column("prices_json", sa.Text(), nullable=True),
        sa.Column("usage_json", sa.Text(), nullable=True),
        sa.Column("normalized_usage_json", sa.Text(), nullable=True),
        sa.Column("cost_cny", sa.Numeric(24, 12), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    for name in ("run_id", "session_id", "status"):
        op.create_index(f"ix_model_usage_requests_{name}", "model_usage_requests", [name])
    op.create_index("ix_model_usage_run_started", "model_usage_requests", ["run_id", "started_at"])


def downgrade():
    op.drop_table("model_usage_requests")
    op.drop_table("model_proxy_sessions")
    op.drop_column("runs", "cost_complete")
    op.drop_column("runs", "cost_source")
