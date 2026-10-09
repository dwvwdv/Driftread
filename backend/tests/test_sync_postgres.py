"""Commit-ordered private invalidations, owner RLS writes and rights revocation."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar, seed_article, assert_blocked


def snapshot(conn, owner, since=None, pending=()):
    return scalar(conn, 'SELECT driftread.personal_sync_snapshot(%s,%s,%s::uuid[])',
                  (str(owner), since, [str(x) for x in pending]))


def subscribed(conn):
    feed, article, _, owner = seed_article(conn)
    with conn.cursor() as cur:
        cur.execute('INSERT INTO driftread.user_feeds(user_id,feed_id) VALUES(%s,%s)', (owner,feed))
    conn.commit()
    return feed, article, owner


def test_owner_data_api_writes_can_record_private_invalidations(pg_database):
    with connection(pg_database) as setup:
        _, article, _, owner = seed_article(setup)
        before = scalar(setup,'SELECT sequence FROM driftread.sync_clock')
        with setup.cursor() as cur:
            cur.execute((Path(__file__).parent/'sql'/'test_sync_permissions.sql').read_text())
    with connection(pg_database,'authenticated') as caller:
        with caller.cursor() as cur:
            cur.execute("SELECT set_config('request.jwt.claims',%s,false)", (json.dumps({'sub':str(owner),'is_anonymous':False}),))
            cur.execute('INSERT INTO driftread.user_article_reads(user_id,article_id) VALUES(%s,%s)', (owner,article))
            cur.execute("INSERT INTO driftread.user_bookmarks(user_id,article_id,bookmark_type) VALUES(%s,%s,'favorite')", (owner,article))
        caller.commit()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            scalar(caller,'SELECT count(*) FROM driftread.sync_changes')
    with connection(pg_database,'service_role') as observer:
        assert scalar(observer,'SELECT sequence FROM driftread.sync_clock') == before + 2
        assert scalar(observer,'SELECT count(*) FROM driftread.sync_changes WHERE sequence>%s AND user_id=%s',(before,owner)) == 2


def test_snapshot_cannot_skip_uncommitted_write_and_rollback_does_not_advance(pg_database):
    with connection(pg_database) as writer, connection(pg_database,'service_role') as reader, connection(pg_database) as observer:
        _, article, owner = subscribed(writer)
        initial = snapshot(reader,owner)['sequence']; reader.commit()
        with writer.cursor() as cur:
            cur.execute("UPDATE driftread.articles SET title='committed title' WHERE id=%s",(article,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(snapshot,reader,owner,initial)
            try:
                assert_blocked(observer,reader.get_backend_pid(),future)
            finally:
                writer.commit()
            result = future.result(timeout=5)
        reader.commit()
        assert result['sequence'] == initial+1
        assert result['changed'] is True
        assert next(r for r in result['items'] if r['id']==str(article))['title']=='committed title'
        assert all('content' not in r and 'search_vector' not in r for r in result['items'])
        with writer.cursor() as cur:
            cur.execute("UPDATE driftread.articles SET title='rolled back' WHERE id=%s",(article,))
        writer.rollback()
        unchanged=snapshot(reader,owner,result['sequence'])
        assert unchanged['sequence']==result['sequence'] and unchanged['changed'] is False


@pytest.mark.parametrize('revocation',['private','signal_only','unsubscribe','mute','delete','rights'])
def test_revocation_replaces_snapshot_and_rechecks_pending_permission(pg_database,revocation):
    with connection(pg_database) as writer, connection(pg_database,'service_role') as reader:
        feed, article, owner=subscribed(writer)
        before=snapshot(reader,owner,pending=[article]); reader.commit()
        assert str(article) in before['authorized_article_ids']
        with writer.cursor() as cur:
            if revocation in {'private','signal_only'}:
                cur.execute('UPDATE driftread.feeds SET participation_mode=%s WHERE id=%s',(revocation,feed))
            elif revocation=='unsubscribe':
                cur.execute('DELETE FROM driftread.user_feeds WHERE user_id=%s AND feed_id=%s',(owner,feed))
            elif revocation=='mute':
                cur.execute('UPDATE driftread.user_feeds SET muted_at=now() WHERE user_id=%s AND feed_id=%s',(owner,feed))
            elif revocation=='rights':
                cur.execute("UPDATE driftread.feeds SET fulltext_policy='summary_only' WHERE id=%s",(feed,))
            else:
                cur.execute('DELETE FROM driftread.articles WHERE id=%s',(article,))
        writer.commit()
        after=snapshot(reader,owner,before['sequence'],[article])
        assert after['changed'] is True
        if revocation=='rights':
            row=next(r for r in after['items'] if r['id']==str(article))
            assert row['fulltext_allowed'] is False and 'content' not in row
        else:
            assert str(article) not in after['authorized_article_ids']
            assert not any(r['id']==str(article) for r in after['items'])


def test_search_is_owner_scoped_and_ledger_window_reset_is_bounded(pg_database):
    with connection(pg_database) as setup, connection(pg_database,'service_role') as reader:
        feed, article, owner=subscribed(setup)
        other=scalar(setup,'INSERT INTO auth.users VALUES(%s) RETURNING id',(str(uuid4()),))
        with setup.cursor() as cur:
            cur.execute("UPDATE driftread.articles SET title='syncneedle' WHERE id=%s",(article,))
        setup.commit()
        assert scalar(reader,'SELECT count(*) FROM driftread.personal_publication_search(%s,%s,1000)',(owner,'syncneedle'))==1
        assert scalar(reader,'SELECT count(*) FROM driftread.personal_publication_search(%s,%s,1000)',(other,'syncneedle'))==0
        reader.commit()
        # A compacted/stale ledger cursor resets the whole bounded recent cache.
        with setup.cursor() as cur:
            cur.execute('UPDATE driftread.sync_clock SET sequence=60000 WHERE singleton')
        setup.commit()
        result=snapshot(reader,owner,0)
        assert result['reset'] is True and result['cache_limit']==100 and len(result['items'])<=100


def test_personal_search_candidates_preserve_rights_and_replay(pg_database):
    root = Path(__file__).parent.parent
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            for _ in range(2):
                cur.execute((root / 'migrations/20261009000700_incremental_sync.sql').read_text())
                cur.execute((Path(__file__).parent / 'sql/test_personal_search_candidates.sql').read_text())


def test_personal_search_selective_query_uses_gin_before_projection(pg_database):
    with connection(pg_database) as conn:
        feed, _, owner = subscribed(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.feeds SET fulltext_policy='summary_only' WHERE id=%s", (feed,))
            cur.execute("""INSERT INTO driftread.articles(feed_id,title,url,summary,content)
                SELECT %s,'history', 'https://search-plan.invalid/'||n,
                  CASE WHEN n=1 THEN 'selectiveneedle' ELSE 'ordinary history' END,
                  'bodyonly '||repeat('cached article text ',100)
                FROM generate_series(1,5000) n""", (feed,))
            cur.execute('ANALYZE driftread.articles; ANALYZE driftread.feeds; ANALYZE driftread.user_feeds')
            # SET search_path prevents SQL-function inlining. Explain the actual
            # catalog body with bound RPC arguments, not a hand-copied query.
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid='driftread.personal_publication_search(uuid,text,int)'::regprocedure")
            body = cur.fetchone()[0]
            for name, placeholder in [('p_user_id', '%(owner)s::uuid'), ('p_query', '%(query)s::text'), ('p_limit', '%(limit)s::int')]:
                body = body.replace(name, placeholder)
            cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + body,
                        {'owner': owner, 'query': 'selectiveneedle', 'limit': 20})
            plan = cur.fetchone()[0][0]['Plan']

        def nodes(node):
            yield node
            for child in node.get('Plans', []):
                yield from nodes(child)

        gin = [node for node in nodes(plan) if node.get('Index Name') == 'articles_search_vector_idx']
        assert len(gin) == 1, json.dumps(plan)
        assert gin[0]['Actual Rows'] == 1 and gin[0]['Actual Loops'] == 1
        excerpt_gin = [node for node in nodes(plan) if node.get('Index Name') == 'articles_excerpt_search_vector_idx']
        assert len(excerpt_gin) == 1, json.dumps(plan)
        assert excerpt_gin[0]['Actual Rows'] == 1 and excerpt_gin[0]['Actual Loops'] == 1
        candidates = [node for node in nodes(plan) if node.get('Subplan Name') == 'CTE candidates']
        assert len(candidates) == 1 and candidates[0]['Actual Rows'] == 1
        assert plan['Actual Rows'] == 1
        assert scalar(conn, 'SELECT count(*) FROM driftread.personal_publication_search(%s,%s)',
                      (owner, 'selectiveneedle')) == 1
