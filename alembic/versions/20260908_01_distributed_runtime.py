"""Add replica safety, fenced runs, and standalone previews."""

from alembic import op


revision = "20260908_01"
down_revision = "20260829_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM notifications newer
        USING notifications older
        WHERE newer.user_id = older.user_id
          AND newer.run_id = older.run_id
          AND newer.kind = older.kind
          AND newer.id <> older.id
          AND (newer.created_at, newer.id) > (older.created_at, older.id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_notification_run_kind'
                  AND conrelid = 'notifications'::regclass
            ) THEN
                ALTER TABLE notifications
                ADD CONSTRAINT uq_notification_run_kind UNIQUE (user_id, run_id, kind);
            END IF;
        END $$
        """
    )

    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS active_generation INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS active_attempt_id VARCHAR(64)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_runs_active_attempt_id ON runs (active_attempt_id)")
    op.execute(
        """CREATE TABLE IF NOT EXISTS run_attempts (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            generation INTEGER NOT NULL,
            status VARCHAR(32) NOT NULL,
            executor_id VARCHAR(255),
            worker_id VARCHAR(255),
            celery_task_id VARCHAR(255),
            reuse_workspace BOOLEAN NOT NULL DEFAULT FALSE,
            lease_until TIMESTAMP,
            heartbeat_at TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            failure_reason TEXT,
            CONSTRAINT uq_run_attempt_run_generation UNIQUE (run_id, generation)
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_attempts_run_id ON run_attempts (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_attempts_status ON run_attempts (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_run_attempts_status_lease_until ON run_attempts (status, lease_until)")

    op.execute(
        """CREATE TABLE IF NOT EXISTS preview_instances (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(64) NOT NULL,
            generation INTEGER NOT NULL,
            status VARCHAR(32) NOT NULL,
            previewer_id VARCHAR(255),
            worker_id VARCHAR(255),
            celery_task_id VARCHAR(255),
            host VARCHAR(255),
            port INTEGER,
            source_revision VARCHAR(128),
            source_snapshot_path VARCHAR(1024) NOT NULL,
            source_snapshot_sha256 VARCHAR(64) NOT NULL,
            workspace_path VARCHAR(1024),
            lease_until TIMESTAMP,
            heartbeat_at TIMESTAMP,
            requested_at TIMESTAMP NOT NULL,
            started_at TIMESTAMP,
            ready_at TIMESTAMP,
            last_accessed_at TIMESTAMP,
            expires_at TIMESTAMP,
            stopped_at TIMESTAMP,
            failure_reason TEXT,
            access_token_hash VARCHAR(64),
            CONSTRAINT uq_preview_instance_run_generation UNIQUE (run_id, generation)
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_preview_instances_run_id ON preview_instances (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_preview_instances_user_id ON preview_instances (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_preview_instances_status ON preview_instances (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_preview_instances_status_lease ON preview_instances (status, lease_until)")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_preview_instances_active_node_port
        ON preview_instances (previewer_id, port)
        WHERE status IN ('STARTING', 'READY', 'STOP_REQUESTED', 'STALE_REQUESTED', 'STOPPING', 'CLEANUP_REQUIRED')
          AND previewer_id IS NOT NULL AND port IS NOT NULL"""
    )

    # QUEUED means no executor owns the attempt. Convert active pre-fencing
    # runs into fresh durable generations and supersede old task payloads.
    op.execute("UPDATE run_attempts SET lease_until = NULL WHERE status = 'QUEUED'")
    op.execute("UPDATE runs SET lease_until = NULL WHERE status = 'QUEUED'")
    op.execute(
        """INSERT INTO run_attempts (
                id, run_id, generation, status, reuse_workspace
            )
            SELECT
                md5('arcbench-migrated-attempt:' || runs.id),
                runs.id,
                GREATEST(runs.active_generation, runs.attempt_count, 0) + 1,
                'QUEUED',
                FALSE
            FROM runs
            WHERE runs.active_attempt_id IS NULL
              AND runs.status IN ('QUEUED', 'STARTING', 'RUNNING', 'RESUME_REQUESTED')
            ON CONFLICT (run_id, generation) DO NOTHING"""
    )
    op.execute(
        """UPDATE runs
            SET status = 'QUEUED',
                active_generation = run_attempts.generation,
                active_attempt_id = run_attempts.id,
                attempt_count = run_attempts.generation,
                worker_id = NULL,
                celery_task_id = NULL,
                lease_until = NULL,
                heartbeat_at = NULL
            FROM run_attempts
            WHERE run_attempts.id = md5('arcbench-migrated-attempt:' || runs.id)
              AND runs.active_attempt_id IS NULL"""
    )
    op.execute(
        """UPDATE task_outbox
            SET sent_at = CURRENT_TIMESTAMP,
                last_error = 'Superseded during RunAttempt migration'
            WHERE sent_at IS NULL
              AND task_name = 'arcbench.run_submission'
              AND NOT (payload_json::jsonb ? 'attempt_id')"""
    )
    op.execute(
        """INSERT INTO task_outbox (
                id, run_id, task_name, payload_json, created_at, attempt_count
            )
            SELECT
                md5('arcbench-migrated-outbox:' || run_attempts.id),
                run_attempts.run_id,
                'arcbench.run_submission',
                json_build_object(
                    'run_id', run_attempts.run_id,
                    'attempt_id', run_attempts.id,
                    'generation', run_attempts.generation,
                    'reuse_workspace', FALSE
                )::text,
                CURRENT_TIMESTAMP,
                0
            FROM run_attempts
            WHERE run_attempts.id = md5('arcbench-migrated-attempt:' || run_attempts.run_id)
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_outbox existing
                  WHERE existing.id = md5('arcbench-migrated-outbox:' || run_attempts.id)
              )"""
    )
    op.execute(
        """UPDATE runs
            SET status = 'PAUSED',
                worker_id = NULL,
                celery_task_id = NULL,
                lease_until = NULL,
                heartbeat_at = NULL
            WHERE active_attempt_id IS NULL
              AND status = 'PAUSE_REQUESTED'"""
    )


def downgrade() -> None:
    # Repaired queue rows may already have executed, so destructive rollback is
    # intentionally unsupported.
    pass
