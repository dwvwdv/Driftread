from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import backfill
import migrate


class _MigrationCursor:
    def __init__(self, applied: set[str]):
        self.applied = applied
        self.pending_result = None
        self.executed_migrations: list[str] = []
        self.executed_statements: list[tuple[str, object]] = []

    def execute(self, statement, params=None):
        self.executed_statements.append((statement, params))
        if statement.lstrip().startswith("SELECT 1 FROM"):
            self.pending_result = params[0] in self.applied
        elif params and statement.lstrip().startswith("INSERT INTO"):
            self.applied.add(params[0])
        elif not params and not statement.lstrip().startswith(
            ("CREATE SCHEMA", "DO $$", "ALTER TABLE", "REVOKE ALL")
        ):
            self.executed_migrations.append(statement)

    def fetchone(self):
        return (1,) if self.pending_result else None


class _MigrationConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = True

    def cursor(self):
        return nullcontext(self._cursor)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_partial_public_schema_upgrade_runs_legacy_sql_before_move(monkeypatch, tmp_path):
    """A database at 005 must be able to run 006-008 before 010 moves it."""
    migrations = Path(migrate.__file__).parent / "migrations"
    for filename in (
        "006_feed_discovery.sql",
        "007_random_feed_sampling.sql",
        "008_hold_discovery_candidates.sql",
        "010_schema_access.sql",
    ):
        (tmp_path / filename).write_text((migrations / filename).read_text())

    cursor = _MigrationCursor({f"00{i}_already_applied.sql" for i in range(1, 6)})
    connection = _MigrationConnection(cursor)
    monkeypatch.setenv("DATABASE_URL", "postgresql://partial-upgrade-fixture")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(migrate.psycopg2, "connect", lambda _url: connection)

    migrate.run_migrations()

    assert cursor.executed_statements[0] == (
        "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
        ("driftread:migrations",),
    )
    ensure_ledger_sql = cursor.executed_statements[2][0]
    assert "CREATE VIEW public._migrations" in ensure_ledger_sql
    assert "FROM driftread._migrations" in ensure_ledger_sql
    migration_006, migration_007, migration_008, migration_010 = cursor.executed_migrations
    assert "ALTER TABLE feeds" in migration_006
    assert "ALTER TABLE driftread.feeds" not in migration_006
    assert "FUNCTION sample_feed_candidates" in migration_007
    assert "ALTER TABLE discovery_candidates" in migration_008
    assert "ALTER TABLE public.%I SET SCHEMA driftread" in migration_010


def test_postgrest_schema_migration_preserves_custom_schemas():
    """The role setting must be extended, not replaced with a fixed list."""
    root = Path(migrate.__file__).parent.parent
    migration = (root / "backend/migrations/010_schema_access.sql").read_text()
    review_layer = (root / "supabase/1_create_schema.sql").read_text()

    for sql in (migration, review_layer):
        assert "FROM pg_roles" in sql
        assert "exposed_schemas := exposed_schemas || ', driftread'" in sql
        assert "SET pgrst.db_schemas = 'public, graphql_public, driftread'" not in sql

    existing = "public, graphql_public, cotime_book"
    resulting = existing + ", driftread"
    assert resulting == "public, graphql_public, cotime_book, driftread"


def test_reviewable_schema_layers_match_deployable_migration():
    """The three review layers must remain the exact source of migration 010."""
    root = Path(migrate.__file__).parent.parent
    layers = [
        root / "supabase/1_create_schema.sql",
        root / "supabase/2_migrate_tables.sql",
        root / "supabase/3_rls_policies.sql",
    ]

    expected = "\n\n".join(path.read_text().strip() for path in layers)
    deployed = (root / "backend/migrations/010_schema_access.sql").read_text().strip()

    assert deployed == expected


def test_backfill_uses_same_lock_before_reading_ledger(monkeypatch):
    cursor = _MigrationCursor({backfill.BACKFILL_NAME})
    connection = _MigrationConnection(cursor)
    monkeypatch.setenv("DATABASE_URL", "postgresql://backfill-lock-fixture")
    monkeypatch.setattr(backfill.psycopg2, "connect", lambda _url: connection)

    backfill.run_backfills()

    assert cursor.executed_statements[:2] == [
        (
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            ("driftread:migrations",),
        ),
        (
            "SELECT 1 FROM driftread._migrations WHERE filename = %s",
            (backfill.BACKFILL_NAME,),
        ),
    ]
