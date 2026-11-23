"""Initialize or upgrade the configured PostgreSQL database."""

from __future__ import annotations

import logging
import time

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from app.core.config import ROOT_DIR, get_settings
from app.db.base import Base
from app.db.schema_validation import verify_current_schema


LOG = logging.getLogger("arcbench.migrate")
MIGRATION_LOCK_ID = 7_289_424_363_141_205_657
CONNECT_ATTEMPTS = 30
CONNECT_RETRY_SECONDS = 2
LEGACY_BRANCH_REVISIONS = {
    "20260907_02",
    "20260907_03",
    "20260907_04",
    "20260907_05",
}


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(ROOT_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def _connect(engine: Engine) -> Connection:
    last_error: Exception | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            connection = engine.connect()
            connection.execute(text("SELECT 1"))
            connection.commit()
            return connection
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOG.warning("database is unavailable (attempt %s/%s)", attempt, CONNECT_ATTEMPTS)
            if attempt < CONNECT_ATTEMPTS:
                time.sleep(CONNECT_RETRY_SECONDS)
    raise RuntimeError("Database did not become available before the migration timeout") from last_error


def _current_revisions(connection: Connection, *, version_table_exists: bool | None = None) -> tuple[str, ...]:
    if version_table_exists is None:
        version_table_exists = "alembic_version" in inspect(connection).get_table_names()
    if not version_table_exists:
        return ()
    return tuple(MigrationContext.configure(connection).get_current_heads())


def _has_outbox_schema(connection: Connection, tables: set[str]) -> bool:
    inspector = inspect(connection)
    run_columns = {column["name"] for column in inspector.get_columns("runs")} if "runs" in tables else set()
    return "task_outbox" in tables or "worker_id" in run_columns


def _stamp(connection: Connection, config: Config, revision: str) -> None:
    # Inspector queries autobegin a transaction on the supplied connection.
    # End it so Alembic can own and commit its migration transaction.
    connection.commit()
    command.stamp(config, revision)
    connection.commit()


def _upgrade(connection: Connection, config: Config, revision: str) -> None:
    connection.commit()
    command.upgrade(config, revision)
    connection.commit()


def migrate_database() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, poolclass=NullPool)
    connection = _connect(engine)
    try:
        connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
        connection.commit()
        config = _alembic_config(connection)
        scripts = ScriptDirectory.from_config(config)
        target_revisions = tuple(scripts.get_heads())
        database_tables = set(inspect(connection).get_table_names())
        version_table_exists = "alembic_version" in database_tables
        current_revisions = _current_revisions(connection, version_table_exists=version_table_exists)
        tables = database_tables - {"alembic_version"}

        if not tables:
            LOG.info("empty database detected; creating the current schema")
            Base.metadata.create_all(bind=connection)
            connection.commit()
            _stamp(connection, config, "head")
        else:
            if version_table_exists:
                if not current_revisions:
                    raise RuntimeError("Database has an Alembic version table but no recorded revision")
                if set(current_revisions).issubset(LEGACY_BRANCH_REVISIONS):
                    LOG.info("consolidating legacy branch revisions: %s", ",".join(current_revisions))
                    connection.execute(text("UPDATE alembic_version SET version_num = '20260829_01'"))
                    connection.commit()
                    current_revisions = ("20260829_01",)
                for revision in current_revisions:
                    if scripts.get_revision(revision) is None:
                        raise RuntimeError(f"Database has an unknown Alembic revision: {revision}")
                LOG.info("database revision: %s; target: %s", ",".join(current_revisions), ",".join(target_revisions))
            else:
                if "runs" not in tables:
                    raise RuntimeError(
                        "Database is neither empty nor a recognized legacy ARC-Bench database; "
                        "the runs table is missing"
                    )
                if _has_outbox_schema(connection, tables):
                    LOG.info("unversioned database includes the Outbox schema; recording baseline")
                    _stamp(connection, config, "20260829_01")
                else:
                    LOG.info("legacy database predates Alembic; applying all migrations")
            _upgrade(connection, config, "head")

        final_revisions = _current_revisions(connection, version_table_exists=True)
        if set(final_revisions) != set(target_revisions):
            raise RuntimeError(
                f"Database migration stopped at {final_revisions or ('unversioned',)}; expected {target_revisions}"
            )
        verify_current_schema(engine)
        LOG.info("database schema is current at revision %s", ",".join(final_revisions))
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            # Discard any read-only transaction opened by revision inspection.
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
            connection.commit()
        finally:
            connection.close()
            engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    migrate_database()


if __name__ == "__main__":
    main()
