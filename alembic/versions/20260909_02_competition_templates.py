"""Add immutable competition template provenance."""

from alembic import op
import sqlalchemy as sa


revision = "20260909_02"
down_revision = "20260909_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("manual_edit_used", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("runs", sa.Column("template_source_run_id", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("template_archive_path", sa.String(512), nullable=True))
    op.add_column("runs", sa.Column("template_archive_sha256", sa.String(64), nullable=True))
    op.create_index("ix_runs_template_source_run_id", "runs", ["template_source_run_id"])
    op.create_table(
        "run_template_artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("competition_id", sa.String(64), nullable=False),
        sa.Column("requirement_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("competition_entry_id", sa.String(32), nullable=True),
        sa.Column("archive_path", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_run_template_artifacts_run_id", "run_template_artifacts", ["run_id"], unique=True)
    op.create_index("ix_run_template_artifacts_competition_id", "run_template_artifacts", ["competition_id"])
    op.create_index("ix_run_template_artifacts_requirement_id", "run_template_artifacts", ["requirement_id"])
    op.create_index("ix_run_template_artifacts_user_id", "run_template_artifacts", ["user_id"])
    op.create_index("ix_run_template_artifacts_competition_entry_id", "run_template_artifacts", ["competition_entry_id"])
    op.create_table(
        "submission_template_sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("submission_id", sa.String(64), sa.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_requirement_id", sa.String(128), nullable=False),
        sa.Column("source_artifact_id", sa.String(64), nullable=False),
        sa.Column("source_run_id", sa.String(64), nullable=False),
        sa.Column("archive_path", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("submission_id", "target_requirement_id", name="uq_submission_template_target"),
    )
    op.create_index("ix_submission_template_sources_submission_id", "submission_template_sources", ["submission_id"])


def downgrade() -> None:
    op.drop_table("submission_template_sources")
    op.drop_table("run_template_artifacts")
    op.drop_index("ix_runs_template_source_run_id", table_name="runs")
    for column in ("template_archive_sha256", "template_archive_path", "template_source_run_id", "manual_edit_used"):
        op.drop_column("runs", column)
