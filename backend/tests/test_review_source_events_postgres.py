"""Review regressions for completion-only source health and archived events."""

from pathlib import Path

from tests.test_postgres_lifecycle import connection, pg_database


ROOT = Path(__file__).parents[1]
FIXTURE = Path(__file__).parent / "sql/test_review_source_events.sql"


def test_success_only_imports_and_archived_event_publications(pg_database):
    with connection(pg_database, "service_role") as conn:
        with conn.cursor() as cur:
            cur.execute(FIXTURE.read_text())


def test_source_and_event_review_migrations_replay(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            for _ in range(2):
                for name in (
                    "20261009000200_source_roles_health.sql",
                    "20261009000500_manual_events.sql",
                ):
                    cur.execute((ROOT / "migrations" / name).read_text())
        conn.commit()
    with connection(pg_database, "service_role") as conn:
        with conn.cursor() as cur:
            cur.execute(FIXTURE.read_text())
