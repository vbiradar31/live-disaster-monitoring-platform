"""Safe, sanitized database target and migration preflight for dev.ps1."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL, make_url


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestion"))


class UnsafeDatabaseTarget(ValueError):
    pass


def validate_database_url(value: str | None) -> URL:
    """Parse and guard a PostgreSQL URL without ever including it in errors."""
    if not value:
        raise UnsafeDatabaseTarget("DATABASE_URL is missing")
    try:
        url = make_url(value)
    except Exception as exc:
        raise UnsafeDatabaseTarget("DATABASE_URL is invalid") from exc
    if url.drivername != "postgresql+psycopg":
        raise UnsafeDatabaseTarget("DATABASE_URL must use postgresql+psycopg")
    if not url.host or not url.database:
        raise UnsafeDatabaseTarget("DATABASE_URL must include a host and database name")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise UnsafeDatabaseTarget("DATABASE_URL has an invalid port")

    database = url.database.lower()
    username = (url.username or "").lower()
    if url.port == 55432 or database.startswith("ldmp_it_") or username == "ldmp_test":
        raise UnsafeDatabaseTarget("Refusing the PostgreSQL integration-test database")
    return url


def sanitized_target(url: URL) -> str:
    return (
        f"host={url.host} port={url.port or 5432} database={url.database}"
    )


def migration_state(url: URL) -> tuple[str | None, list[str]]:
    """Read current and pending Alembic revisions in a read-only transaction."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("BEGIN READ ONLY")
            try:
                current = MigrationContext.configure(connection).get_current_revision()
                if current is None:
                    existing = set(inspect(connection).get_table_names(schema="public")) - {
                        "alembic_version"
                    }
                    if existing:
                        raise UnsafeDatabaseTarget(
                            "No Alembic revision exists but public application tables are present; refusing migration"
                        )
            finally:
                connection.exec_driver_sql("ROLLBACK")
    finally:
        engine.dispose()

    upper = "heads"
    lower = current or "base"
    pending_scripts = list(
        reversed(list(scripts.iterate_revisions(upper, lower, implicit_base=True)))
    )
    return current, [script.revision for script in pending_scripts]


def verify_integration_service(value: str | None) -> None:
    """Authenticate only to the explicitly named disposable test endpoint."""
    if not value:
        raise UnsafeDatabaseTarget("Integration test URL is missing")
    try:
        url = make_url(value)
    except Exception as exc:
        raise UnsafeDatabaseTarget("Integration test URL is invalid") from exc
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 55432
        or url.database != "postgres"
        or url.username != "ldmp_test"
    ):
        raise UnsafeDatabaseTarget("Integration service URL did not match the isolated test endpoint")

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 4})
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("BEGIN READ ONLY")
            try:
                identity = connection.exec_driver_sql(
                    "SELECT current_database(), current_user"
                ).one()
                if tuple(identity) != ("postgres", "ldmp_test"):
                    raise UnsafeDatabaseTarget("Connected PostgreSQL identity was not the isolated test endpoint")
            finally:
                connection.exec_driver_sql("ROLLBACK")
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-only", action="store_true")
    parser.add_argument("--migration-preflight", action="store_true")
    parser.add_argument("--integration-preflight", action="store_true")
    args = parser.parse_args()
    if sum((args.target_only, args.migration_preflight, args.integration_preflight)) != 1:
        parser.error("choose exactly one preflight mode")

    if args.integration_preflight:
        try:
            verify_integration_service(os.environ.get("POSTGRES_TEST_ADMIN_URL"))
        except UnsafeDatabaseTarget as exc:
            print(f"Integration database check failed: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Integration database check failed ({type(exc).__name__}).", file=sys.stderr)
            return 2
        print("Verified the isolated PostgreSQL integration service.")
        return 0

    try:
        from app.config import settings

        url = validate_database_url(settings.database_url)
        print(f"Development database target: {sanitized_target(url)}")
        if args.migration_preflight:
            current, pending = migration_state(url)
            print(f"Current Alembic revision: {current or '(none)'}")
            print(f"Pending Alembic revisions: {', '.join(pending) if pending else '(none)'}")
    except UnsafeDatabaseTarget as exc:
        print(f"Development database check failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Driver and settings exceptions may embed connection details. Report
        # only the exception type, never its text or the resolved URL.
        print(f"Development database preflight failed ({type(exc).__name__}); migration was not run.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
