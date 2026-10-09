from pathlib import Path
import psycopg2
from tests.test_postgres_lifecycle import pg_database, connection


def test_publication_rights_visibility_and_user_isolation(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / "sql/test_publication_read_layer.sql").read_text())


def test_anonymous_cannot_read_raw_articles_or_internal_publication(pg_database):
    import pytest
    for table in ("articles", "article_publications"):
        with connection(pg_database, "anon") as conn:
            with conn.cursor() as cur, pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(f"SELECT id FROM driftread.{table} LIMIT 1")


def test_publication_and_foundation_migration_replay(pg_database, monkeypatch):
    from migrate import run_migrations
    root = Path(__file__).parent.parent
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((root / "migrations/20261009000300_publication_read_layer.sql").read_text())
            cur.execute("DELETE FROM driftread._migrations WHERE filename >= %s", ("20261009000000",))
        conn.commit()
    monkeypatch.setenv("DATABASE_URL", pg_database)
    run_migrations()
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / "sql/test_publication_read_layer.sql").read_text())
