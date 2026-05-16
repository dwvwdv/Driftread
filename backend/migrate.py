from __future__ import annotations
import logging
import os
from pathlib import Path

import psycopg2

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set — skipping auto-migration")
        return

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as e:
        logger.error(
            "Migration skipped — could not connect to database: %s\n"
            "Ensure DATABASE_URL uses the Session Pooler URL (IPv4) from\n"
            "Supabase Dashboard → Settings → Database → Connection pooling → Session mode",
            e,
        )
        return

    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.commit()

            for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
                cur.execute("SELECT 1 FROM _migrations WHERE filename = %s", (sql_file.name,))
                if cur.fetchone():
                    continue
                logger.info("Applying migration: %s", sql_file.name)
                cur.execute(sql_file.read_text())
                cur.execute("INSERT INTO _migrations (filename) VALUES (%s)", (sql_file.name,))
                conn.commit()
                logger.info("Applied: %s", sql_file.name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
