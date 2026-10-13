"""The additional kind reuses the queue's transaction and lease invariants."""
from pathlib import Path
from uuid import uuid4

from tests.test_postgres_lifecycle import pg_database, connection, scalar

MIGRATION = Path(__file__).parents[1] / 'migrations/20261013040010_source_review_queue.sql'


def test_source_review_registration_replays_without_enabling_or_enqueuing(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION.read_text())
            cur.execute(MIGRATION.read_text())
        assert scalar(conn, "SELECT max_concurrency FROM driftread.background_job_kinds WHERE kind='source_review'") == 1
        assert scalar(conn, "SELECT count(*) FROM driftread.background_jobs WHERE kind='source_review'") == 0
        assert not scalar(conn, "SELECT coalesce((SELECT (value->>'enabled')::boolean FROM driftread.app_settings WHERE key='source.review'),false)")


def test_source_review_singleton_capacity_fencing_and_worker_ledger(pg_database):
    with connection(pg_database, 'service_role') as conn:
        key = str(uuid4())
        job = scalar(conn, "SELECT driftread.enqueue_background_job('source_review','{}',%s)", (key,))
        assert scalar(conn, "SELECT driftread.enqueue_background_job('source_review','{}',%s)", (key,)) == job
        with conn.cursor() as cur:
            worker = str(uuid4())
            cur.execute("SELECT id,lease_token FROM driftread.claim_background_job('source_review',%s)", (worker,))
            first = cur.fetchone()
        assert first and str(first[0]) == str(job)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM driftread.claim_background_job('source_review',%s)", (str(uuid4()),))
            assert cur.fetchone() is None
            cur.execute("INSERT INTO driftread.worker_runs(id,worker_id,kind,status,started_at) VALUES(%s,%s,'source_review','running',now())", (str(uuid4()), worker))
        assert scalar(conn, 'SELECT driftread.finish_background_job(%s,%s,true)', (job, str(uuid4()))) is False
        assert scalar(conn, 'SELECT driftread.finish_background_job(%s,%s,true)', first) is True
        conn.rollback()
