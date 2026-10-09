"""The final release migration notifies PostgREST only after successful commit."""

from pathlib import Path
import select

import pytest

import migrate
from tests.test_postgres_lifecycle import connection, pg_database


MIGRATIONS = Path(__file__).parents[1] / "migrations"
FINAL = "20261009000700_incremental_sync.sql"
FIXTURE = Path(__file__).parent / "sql/test_schema_reload.sql"


def listen(conn):
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("LISTEN pgrst")


def assert_reload(conn):
    conn.poll()
    if not conn.notifies:
        assert select.select([conn], [], [], 3)[0], "No committed schema reload received"
        conn.poll()
    assert [(n.channel, n.payload) for n in conn.notifies] == [("pgrst", "reload schema")]
    conn.notifies.clear()
    with conn.cursor() as cur:
        cur.execute(FIXTURE.read_text())


@pytest.mark.parametrize("commit", [False, True])
def test_final_migration_reload_waits_for_commit(pg_database, commit):
    assert sorted(MIGRATIONS.glob("*.sql"))[-1].name == FINAL
    with connection(pg_database) as listener, connection(pg_database) as writer:
        listen(listener)
        with writer.cursor() as cur:
            cur.execute((MIGRATIONS / FINAL).read_text())
        listener.poll()
        assert not listener.notifies
        if commit:
            writer.commit()
            assert_reload(listener)
        else:
            writer.rollback()
            assert not select.select([listener], [], [], 0.05)[0]
            listener.poll()
            assert not listener.notifies


def test_real_migration_runner_reloads_after_final_ddl_and_is_idempotent(pg_database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_database)
    with connection(pg_database) as listener, connection(pg_database) as setup:
        listen(listener)
        # Keep historical ledgers; recreate only this unmerged release tail.
        with setup.cursor() as cur:
            cur.execute("DELETE FROM driftread._migrations WHERE filename=%s", (FINAL,))
            cur.execute("DROP FUNCTION driftread.replay_personal_article_state(uuid,uuid,text,boolean)")
        setup.commit()
        migrate.run_migrations()
        assert_reload(listener)
        migrate.run_migrations()
        listener.poll()
        assert not listener.notifies
