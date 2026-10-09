from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar, assert_blocked
from tests.test_sync_postgres import subscribed, snapshot
from tests.test_postgres_lifecycle import seed_article


def replay(conn, user, article, kind='read', enabled=True):
    outcome = scalar(conn, 'SELECT driftread.replay_personal_article_state(%s,%s,%s,%s)',
                     (user, article, kind, enabled))
    assert outcome in ('applied', 'unavailable')
    return outcome == 'applied'


def revoke(conn, user, feed, reason):
    with conn.cursor() as cur:
        if reason in ('private', 'signal_only'):
            cur.execute('UPDATE driftread.feeds SET participation_mode=%s WHERE id=%s', (reason, feed))
        elif reason == 'mute':
            cur.execute('UPDATE driftread.user_feeds SET muted_at=now() WHERE user_id=%s AND feed_id=%s', (user, feed))
        else:
            cur.execute('DELETE FROM driftread.user_feeds WHERE user_id=%s AND feed_id=%s', (user, feed))


@pytest.mark.parametrize('reason', ['private', 'signal_only', 'mute', 'unsubscribe'])
@pytest.mark.parametrize('kind', ['read', 'favorite', 'read_later'])
def test_revocation_after_preflight_rejects_replay_without_persisting_intent(pg_database, reason, kind):
    with connection(pg_database) as setup:
        feed, article, user = subscribed(setup)
        assert str(article) in snapshot(setup, user, pending=[article])['authorized_article_ids']
        setup.commit()
        revoke(setup, user, feed, reason)
        setup.commit()
    with connection(pg_database, 'service_role') as writer:
        assert replay(writer, user, article, kind) is False
        writer.commit()
        assert scalar(writer, 'SELECT count(*) FROM driftread.user_article_reads WHERE user_id=%s AND article_id=%s', (user, article)) == 0
        assert scalar(writer, 'SELECT count(*) FROM driftread.user_bookmarks WHERE user_id=%s AND article_id=%s', (user, article)) == 0


@pytest.mark.parametrize('reason', ['private', 'mute', 'unsubscribe'])
def test_replay_waits_for_inflight_revocation_and_rechecks_authorization(pg_database, reason):
    with connection(pg_database) as setup:
        feed, article, user = subscribed(setup)
    with connection(pg_database) as revoker, connection(pg_database, 'service_role') as writer, connection(pg_database) as observer:
        pid = scalar(writer, 'SELECT pg_backend_pid()')
        revoke(revoker, user, feed, reason)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(replay, writer, user, article)
            try:
                assert_blocked(observer, pid, future)
            finally:
                revoker.commit()
            assert future.result(timeout=3) is False
        writer.commit()


def test_replay_holds_authorization_until_commit_and_is_idempotent_owner_scoped(pg_database):
    with connection(pg_database) as setup:
        feed, article, user = subscribed(setup)
        other = scalar(setup, 'INSERT INTO auth.users VALUES(%s) RETURNING id', (str(uuid4()),))
        setup.commit()
    with connection(pg_database, 'service_role') as writer, connection(pg_database) as revoker, connection(pg_database) as observer:
        assert replay(writer, other, article) is False
        for kind in ('read', 'favorite', 'read_later'):
            assert replay(writer, user, article, kind) is True
            assert replay(writer, user, article, kind) is True
        pid = scalar(revoker, 'SELECT pg_backend_pid()')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(revoke, revoker, user, feed, 'mute')
            try:
                assert_blocked(observer, pid, future)
            finally:
                writer.commit()
            future.result(timeout=3)
        revoker.rollback()
        assert scalar(observer, 'SELECT count(*) FROM driftread.user_article_reads WHERE user_id=%s AND article_id=%s', (user, article)) == 1
        assert scalar(observer, 'SELECT count(*) FROM driftread.user_bookmarks WHERE user_id=%s AND article_id=%s', (user, article)) == 2
        for kind in ('read', 'favorite', 'read_later'):
            assert replay(writer, user, article, kind, False) is True
            assert replay(writer, user, article, kind, False) is True
        writer.commit()
        assert scalar(observer, 'SELECT count(*) FROM driftread.user_bookmarks WHERE user_id=%s AND article_id=%s', (user, article)) == 0
    for role in ('anon', 'authenticated'):
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                replay(conn, user, article)


def test_batch_update_contention_rolls_back_intent_and_ledger_then_can_retry(pg_database):
    with connection(pg_database) as setup:
        _, first, _, _ = seed_article(setup)
        _, article, user = subscribed(setup)
    with connection(pg_database) as batch, connection(pg_database, 'service_role') as writer, connection(pg_database) as observer:
        pid = scalar(writer, 'SELECT pg_backend_pid()')
        before = scalar(observer, 'SELECT sequence FROM driftread.sync_clock')
        scalar(batch, "UPDATE driftread.articles SET title='first update' WHERE id=%s RETURNING id", (first,))
        def replay_raw():
            return scalar(writer, 'SELECT driftread.replay_personal_article_state(%s,%s,%s,%s)', (user, article, 'favorite', True))
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(replay_raw)
            assert_blocked(observer, pid, future)
            second = pool.submit(scalar, batch, "UPDATE driftread.articles SET title='second update' WHERE id=%s RETURNING id", (article,))
            assert future.result(timeout=3) == 'retry'
            assert str(second.result(timeout=3)) == str(article)
        writer.commit()
        batch.commit()
        assert scalar(observer, 'SELECT count(*) FROM driftread.user_bookmarks WHERE user_id=%s AND article_id=%s', (user, article)) == 0
        assert scalar(observer, "SELECT count(*) FROM driftread.sync_changes WHERE user_id=%s AND entity='user_bookmarks' AND sequence>%s", (user, before)) == 0
        assert replay(writer, user, article, 'favorite') is True
        writer.commit()
        assert scalar(observer, 'SELECT count(*) FROM driftread.user_bookmarks WHERE user_id=%s AND article_id=%s', (user, article)) == 1
