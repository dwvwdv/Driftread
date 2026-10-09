from pathlib import Path
from tests.test_postgres_lifecycle import pg_database, connection, scalar

def test_source_roles_and_fetch_timestamps(pg_database):
    with connection(pg_database) as conn:
        conn.commit();conn.autocommit=True
        with conn.cursor() as cur: cur.execute((Path(__file__).parent/'sql'/'test_source_model.sql').read_text())

def test_source_migration_replays_without_ledger(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            migration=(Path(__file__).parents[1]/'migrations'/'20261009000200_source_roles_health.sql').read_text()
            cur.execute(migration);cur.execute(migration)
        assert scalar(conn,"SELECT count(*) FROM pg_constraint WHERE conname='feeds_participation_mode_check'")==1
