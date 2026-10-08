from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar


def enqueue(conn, kind='refresh', key=None, attempts=3, repeat=0):
    return scalar(conn, "SELECT driftread.enqueue_background_job(%s,'{}',%s,now(),%s,%s)",
                  (kind, key, attempts, repeat))


def claim(conn, kind='refresh'):
    with conn.cursor() as cur:
        cur.execute('SELECT id,lease_token,attempts FROM driftread.claim_background_job(%s,%s)',
                    (kind, str(uuid4())))
        return cur.fetchone()


@pytest.fixture(autouse=True)
def clean_queue(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE driftread.background_jobs,driftread.worker_alerts,driftread.worker_runs,driftread.worker_heartbeats')
        conn.commit()


def test_sql_fixture_and_replay(pg_database):
    with connection(pg_database) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((Path(__file__).parent / 'sql/test_background_jobs.sql').read_text())
            # A deployment with an erased ledger can replay both new migrations.
            for name in ('20261009000000_background_queue.sql','20261009000100_worker_watchdog.sql'):
                cur.execute((Path(__file__).parents[1] / 'migrations' / name).read_text())


def test_enqueue_rolls_back_with_domain_transaction(pg_database):
    with connection(pg_database, 'service_role') as producer, connection(pg_database) as observer:
        job = enqueue(producer, key=str(uuid4()))
        assert scalar(observer,'SELECT count(*) FROM driftread.background_jobs WHERE id=%s',(job,)) == 0
        producer.rollback()
        assert scalar(observer,'SELECT count(*) FROM driftread.background_jobs WHERE id=%s',(job,)) == 0


def test_two_connections_enforce_kind_capacity_and_singleton(pg_database):
    ready = Barrier(2)
    key = str(uuid4())
    def produce():
        with connection(pg_database, 'service_role') as conn:
            ready.wait(3)
            job = enqueue(conn, key=key)
            conn.commit()
            return job
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: produce(), range(2)))
    assert ids[0] == ids[1]
    with connection(pg_database, 'service_role') as conn:
        enqueue(conn, key=str(uuid4()))
        conn.commit()
    ready.reset()
    def consume():
        with connection(pg_database, 'service_role') as conn:
            ready.wait(3)
            job = claim(conn)
            conn.commit()
            return job
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: consume(), range(2)))
    assert sum(job is not None for job in claims) == 1


def test_expired_lease_retries_and_fences_old_ack(pg_database):
    with connection(pg_database, 'service_role') as conn:
        job = enqueue(conn, attempts=2)
        first = claim(conn)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.background_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(job,))
        assert scalar(conn,'SELECT driftread.renew_background_job(%s,%s)',first[:2]) is False
        assert scalar(conn,'SELECT driftread.finish_background_job(%s,%s,true)',first[:2]) is False
        assert scalar(conn,'SELECT driftread.reconcile_background_jobs()') == 1
        assert claim(conn) is None  # persisted backoff
        with conn.cursor() as cur:
            cur.execute('UPDATE driftread.background_jobs SET available_at=now() WHERE id=%s',(job,))
        second = claim(conn)
        assert second[2] == 2 and second[1] != first[1]
        assert scalar(conn,'SELECT driftread.finish_background_job(%s,%s,true)',first[:2]) is False
        assert scalar(conn,"SELECT driftread.finish_background_job(%s,%s,false,'RuntimeError')",second[:2]) is True
        assert scalar(conn,'SELECT status FROM driftread.background_jobs WHERE id=%s',(job,)) == 'dead'
        conn.commit()


def test_concurrency_two_and_locked_head_are_supported(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.background_job_kinds SET max_concurrency=2 WHERE kind='refresh'")
        head = enqueue(conn)
        enqueue(conn)
        conn.commit()
    try:
        with connection(pg_database) as locked, connection(pg_database, 'service_role') as consumer:
            scalar(locked,'SELECT id FROM driftread.background_jobs WHERE id=%s FOR UPDATE',(head,))
            job = claim(consumer)
            assert job and job[0] != head
            consumer.commit()
    finally:
        with connection(pg_database) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE driftread.background_job_kinds SET max_concurrency=1 WHERE kind='refresh'")
            conn.commit()


def seed_worker(conn, status='running', stale=True):
    worker = str(uuid4())
    run = str(uuid4())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO driftread.worker_heartbeats VALUES(%s,'host',1,now(),now()-make_interval(secs=>%s),%s,'{}')",
                    (worker, 100 if stale else 0, status))
        cur.execute("INSERT INTO driftread.worker_runs(id,worker_id,kind,status,started_at) VALUES(%s,%s,'refresh','running',now())",(run,worker))
    return worker,run


def test_watchdog_persists_deduplicates_and_recovers(pg_database):
    with connection(pg_database, 'service_role') as conn:
        worker, run = seed_worker(conn)
        first = scalar(conn,'SELECT driftread.watchdog_worker_operations()')
        assert first['new_alerts'] == 1 and first['interrupted'] == 1
        assert scalar(conn,'SELECT status FROM driftread.worker_runs WHERE id=%s',(run,)) == 'interrupted'
        assert scalar(conn,'SELECT driftread.watchdog_worker_operations()')['new_alerts'] == 0
        with conn.cursor() as cur:
            cur.execute('UPDATE driftread.worker_heartbeats SET heartbeat_at=now() WHERE worker_id=%s',(worker,))
        assert scalar(conn,'SELECT driftread.watchdog_worker_operations()')['recovered'] == 1
        assert scalar(conn,'SELECT resolved_at IS NOT NULL FROM driftread.worker_alerts WHERE worker_id=%s',(worker,))
        seed_worker(conn, status='stopped')
        assert scalar(conn,'SELECT driftread.watchdog_worker_operations()')['new_alerts'] == 0
        conn.commit()


@pytest.mark.parametrize('role',['anon','authenticated'])
def test_private_tables_and_rpcs_reject_real_roles(pg_database,role):
    with connection(pg_database,role) as conn:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn,'SELECT driftread.claim_background_job(%s,%s)',('refresh',str(uuid4())))
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(conn,'SELECT count(*) FROM driftread.worker_alerts')


def test_watchdog_two_api_connections_create_one_durable_incident(pg_database):
    with connection(pg_database) as conn:
        seed_worker(conn)
        conn.commit()
    ready=Barrier(2)
    def sweep():
        with connection(pg_database,'service_role') as conn:
            ready.wait(3)
            result=scalar(conn,'SELECT driftread.watchdog_worker_operations()')
            conn.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda _:sweep(),range(2)))
    assert sum(outcome['new_alerts'] for outcome in outcomes)==1
    with connection(pg_database) as conn:
        assert scalar(conn,'SELECT count(*) FROM driftread.worker_alerts')==1
        assert scalar(conn,"SELECT count(*) FROM driftread.worker_runs WHERE status='interrupted'")==1


def test_sweep_is_bounded_and_dead_recurring_cycle_gets_next_occurrence(pg_database):
    with connection(pg_database,'service_role') as conn:
        for kind in ('refresh','discovery','retention'):
            enqueue(conn,kind,key='bounded',attempts=1,repeat=60)
            claim(conn,kind)
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.background_jobs SET lease_expires_at=now()-interval '1 second' WHERE status='running'")
        assert scalar(conn,'SELECT driftread.reconcile_background_jobs(1)')==1
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='running'")==2
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='dead'")==1
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='queued' AND attempts=0")==1
        assert scalar(conn,'SELECT driftread.reconcile_background_jobs(100)')==2
        conn.commit()


def test_watchdog_prunes_only_old_successes_and_resolved_alerts(pg_database):
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO driftread.background_jobs(kind,status,finished_at)
                VALUES('refresh','succeeded',now()-interval '31 days'),
                      ('refresh','dead',now()-interval '31 days'),
                      ('refresh','queued',NULL)""")
            cur.execute("""INSERT INTO driftread.worker_alerts(worker_id,kind,resolved_at)
                VALUES('old','worker_stale',now()-interval '31 days'),('unresolved','worker_stale',NULL)""")
        scalar(conn,'SELECT driftread.watchdog_worker_operations()')
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='succeeded'")==0
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='dead'")==1
        assert scalar(conn,"SELECT count(*) FROM driftread.background_jobs WHERE status='queued'")==1
        assert scalar(conn,'SELECT count(*) FROM driftread.worker_alerts')==1
        conn.commit()


def test_due_priority_and_periodic_metadata_are_preserved(pg_database):
    with connection(pg_database,'service_role') as conn:
        low=scalar(conn,"SELECT driftread.enqueue_background_job('refresh','{}','low',now()-interval '1 day',3,60,-10,900)")
        high=scalar(conn,"SELECT driftread.enqueue_background_job('refresh','{}','high',now(),3,60,10,1800)")
        job=claim(conn)
        assert job[0]==high
        assert scalar(conn,'SELECT driftread.finish_background_job(%s,%s,true)',job[:2]) is True
        assert scalar(conn,"SELECT priority=10 AND timeout_seconds=1800 FROM driftread.background_jobs WHERE singleton_key='high' AND status='queued'") is True
        assert claim(conn)[0]==low
        conn.commit()


def test_watchdog_allows_initial_heartbeat_grace_for_a_new_run(pg_database):
    with connection(pg_database) as conn:
        run=str(uuid4())
        with conn.cursor() as cur:
            cur.execute("INSERT INTO driftread.worker_runs VALUES(%s,'new-worker','refresh','running',now(),NULL,'{}',NULL)",(run,))
        assert scalar(conn,'SELECT driftread.watchdog_worker_operations()')['interrupted']==0
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.worker_runs SET started_at=now()-interval '91 seconds' WHERE id=%s",(run,))
        assert scalar(conn,'SELECT driftread.watchdog_worker_operations()')['interrupted']==1
        conn.commit()


def test_restarted_scheduler_updates_future_interval_without_losing_singleton(pg_database):
    with connection(pg_database,'service_role') as conn:
        job=scalar(conn,"SELECT driftread.enqueue_background_job('refresh','{}','scheduler',now()+interval '1 hour',3,300,10,900)")
        same=scalar(conn,"SELECT driftread.enqueue_background_job('refresh','{}','scheduler',now(),3,600,NULL,1800)")
        assert same==job
        assert scalar(conn,'SELECT repeat_seconds=600 AND priority=10 AND timeout_seconds=1800 AND available_at>now() FROM driftread.background_jobs WHERE id=%s',(job,))
        # Generic dedup without new scheduling preferences does not erase them.
        scalar(conn,"SELECT driftread.enqueue_background_job('refresh','{}','scheduler')")
        assert scalar(conn,'SELECT repeat_seconds FROM driftread.background_jobs WHERE id=%s',(job,))==600
        conn.commit()
