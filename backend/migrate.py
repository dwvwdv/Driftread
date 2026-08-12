from __future__ import annotations
import logging
import os
from pathlib import Path

import psycopg2

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SCHEMA = "driftread"
_MIGRATIONS_TABLE = f"{_SCHEMA}._migrations"
_MIGRATION_LOCK_NAME = "driftread:migrations"


def acquire_migration_lock(cur) -> None:
    """Serialize schema migrations and ledger-backed data backfills.

    This is a session-level lock, so it remains held across the per-migration
    commits below and is released automatically when the connection closes.
    """
    cur.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
        (_MIGRATION_LOCK_NAME,),
    )


def _ensure_migration_table(cur) -> None:
    """Create the private migration ledger, preserving legacy public history."""
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
    cur.execute(
        f"""
        DO $$
        BEGIN
          IF to_regclass('{_MIGRATIONS_TABLE}') IS NULL THEN
            IF EXISTS (
              SELECT 1
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'public'
                AND c.relname = '_migrations'
                AND c.relkind IN ('r', 'p')
            ) THEN
              ALTER TABLE public._migrations SET SCHEMA {_SCHEMA};
            ELSE
              CREATE TABLE {_MIGRATIONS_TABLE} (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
              );
            END IF;
          END IF;

          -- Create the rollback bridge before any historical migration runs.
          -- If 006-008 fails before 010, a legacy image must still see the
          -- real ledger instead of creating a new empty public table.
          IF to_regclass('public._migrations') IS NULL THEN
            CREATE VIEW public._migrations
              WITH (security_invoker = true)
              AS SELECT filename, applied_at FROM {_MIGRATIONS_TABLE};
          END IF;
        END
        $$
        """
    )

    # The app schema is exposed to PostgREST. The ledger is not an API surface:
    # RLS plus explicit revokes keep it inaccessible even if grants drift later.
    cur.execute(f"ALTER TABLE {_MIGRATIONS_TABLE} ENABLE ROW LEVEL SECURITY")
    cur.execute(f"REVOKE ALL ON TABLE {_MIGRATIONS_TABLE} FROM PUBLIC, anon, authenticated")
    cur.execute("REVOKE ALL ON TABLE public._migrations FROM PUBLIC, anon, authenticated")


def run_migrations() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set — skipping auto-migration")
        return

    conn = psycopg2.connect(db_url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            acquire_migration_lock(cur)
            _ensure_migration_table(cur)
            conn.commit()

            for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(
                    f"SELECT 1 FROM {_MIGRATIONS_TABLE} WHERE filename = %s",
                    (sql_file.name,),
                )
                if cur.fetchone():
                    continue
                logger.info("Applying migration: %s", sql_file.name)
                cur.execute(sql_file.read_text())
                cur.execute(
                    f"INSERT INTO {_MIGRATIONS_TABLE} (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (sql_file.name,),
                )
                conn.commit()
                logger.info("Applied: %s", sql_file.name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
