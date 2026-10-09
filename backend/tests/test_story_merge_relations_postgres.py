from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.test_postgres_lifecycle import assert_blocked, connection, pg_database, scalar


FIXTURE = Path(__file__).parent / "sql/test_story_merge_relations.sql"


def test_merge_preserves_relations_and_rejects_conflicts_atomically(pg_database):
    with connection(pg_database, "service_role") as conn:
        with conn.cursor() as cur:
            cur.execute(FIXTURE.read_text())


def test_relation_merge_migration_replays(pg_database):
    migration = Path(__file__).parents[1] / "migrations/20261009000500_manual_events.sql"
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(migration.read_text())
            cur.execute(migration.read_text())
        conn.commit()
    with connection(pg_database, "service_role") as conn:
        with conn.cursor() as cur:
            cur.execute(FIXTURE.read_text())


@pytest.mark.parametrize("first", ["merge", "relation"])
def test_concurrent_relation_patch_and_merge_preserve_canonical_edge(pg_database, first):
    with connection(pg_database) as setup:
        source = scalar(setup, "INSERT INTO driftread.event_objects(kind,title) VALUES('story','Merge source') RETURNING id")
        target = scalar(setup, "INSERT INTO driftread.event_objects(kind,title) VALUES('story','Merge target') RETURNING id")
        peer = scalar(setup, "INSERT INTO driftread.event_objects(kind,title) VALUES('story','Relation author') RETURNING id")
        setup.commit()

    def merge(conn):
        return scalar(conn, "SELECT driftread.merge_manual_stories(%s,%s,1,1)", (source, target))

    def relate(conn):
        return scalar(conn, "SELECT driftread.mutate_manual_event(%s,1,NULL,NULL,%s,'SAME_STORY')", (peer, source))

    with connection(pg_database, "service_role") as holder, connection(pg_database, "service_role") as waiter, connection(pg_database) as observer:
        start, finish = (merge, relate) if first == "merge" else (relate, merge)
        start(holder)

        def pending():
            result = finish(waiter)
            waiter.commit()
            return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(pending)
            try:
                assert_blocked(observer, waiter.get_backend_pid(), future)
            finally:
                holder.commit()
            assert future.result(timeout=5)["version"] == 2
        assert scalar(observer, "SELECT count(*) FROM driftread.event_relations WHERE left_id=%s OR right_id=%s", (source, source)) == 0
        assert scalar(observer, "SELECT relation FROM driftread.event_relations WHERE left_id=least(%s::uuid,%s::uuid) AND right_id=greatest(%s::uuid,%s::uuid)", (target, peer, target, peer)) == "SAME_STORY"
