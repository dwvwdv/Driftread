"""#114: identical items with a restamped pubDate must not rewrite articles."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg2.extras import Json

from tests.test_postgres_lifecycle import pg_database, connection, scalar

T0 = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def _feed(conn):
    return str(scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('amplification',%s) RETURNING id",
                      (f'https://{uuid4().hex}.example/rss',)))


def _ingest(conn, fid, published_at=T0, **overrides):
    row = dict(title='headline', url='https://news.example/a', summary='lede', author='desk',
               content='body', content_hash='a' * 64,
               published_at=published_at.isoformat() if published_at else None)
    row.update(overrides)
    return scalar(conn, 'SELECT driftread.ingest_article_batch(%s,%s)', (fid, Json([row])))


def _state(conn, fid):
    with conn.cursor() as cur:
        cur.execute('''SELECT a.id,a.ctid::text,a.published_at,a.revision_number,
            (SELECT count(*) FROM driftread.article_revisions r WHERE r.article_id=a.id),
            (SELECT count(*) FROM driftread.sync_changes WHERE entity='articles')
            FROM driftread.articles a WHERE a.feed_id=%s''', (fid,))
        return cur.fetchone()


def _provenance(conn, article):
    with conn.cursor() as cur:
        cur.execute('SELECT ctid::text,observed_hash,last_seen_at FROM driftread.article_discoveries WHERE article_id=%s',
                    (article,))
        return cur.fetchone()


def test_restamped_pubdate_does_not_rewrite_revision_or_invalidate_sync(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        fid = _feed(conn)
        assert _ingest(conn, fid) == 1
        article, ctid, published, revision, revisions, syncs = _state(conn, fid)
        # A generator that restamps every item on each 15-minute refresh.
        for step in range(1, 21):
            assert _ingest(conn, fid, T0 + timedelta(minutes=15 * step)) == 0
        assert _state(conn, fid) == (article, ctid, published, revision, revisions, syncs)
        assert published == T0

        # Dropping the date is not a correction either.
        assert _ingest(conn, fid, None) == 0
        assert _state(conn, fid)[2] == T0


def test_real_changes_still_carry_the_current_source_date(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        fid = _feed(conn)
        _ingest(conn, fid)
        corrected = T0 + timedelta(hours=3)
        assert _ingest(conn, fid, corrected, title='headline (corrected)') == 1
        _, _, published, revision, revisions, _ = _state(conn, fid)
        assert (published, revision, revisions) == (corrected, 2, 2)

        revised = T0 + timedelta(hours=5)
        assert _ingest(conn, fid, revised, title='headline (corrected)',
                       content='revised body', content_hash='b' * 64) == 1
        _, _, published, revision, revisions, _ = _state(conn, fid)
        assert (published, revision, revisions) == (revised, 3, 3)
        with conn.cursor() as cur:
            cur.execute('SELECT content,content_compacted_at,discovery_extracted_at FROM driftread.articles WHERE feed_id=%s',
                        (fid,))
            assert cur.fetchone() == ('revised body', None, None)

        for field, value in (('summary', 'new lede'), ('author', 'another desk')):
            assert _ingest(conn, fid, revised, title='headline (corrected)', content='revised body',
                           content_hash='b' * 64, **{field: value}) == 1
            # Keep later iterations on the latest metadata.
            with conn.cursor() as cur:
                cur.execute(f'SELECT {field} FROM driftread.articles WHERE feed_id=%s', (fid,))
                assert cur.fetchone()[0] == value


def test_missing_date_is_filled_once_then_becomes_canonical(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        fid = _feed(conn)
        assert _ingest(conn, fid, None) == 1
        assert _ingest(conn, fid, None) == 0
        assert _ingest(conn, fid, T0) == 1
        _, _, published, revision, _, _ = _state(conn, fid)
        assert (published, revision) == (T0, 2)
        assert _ingest(conn, fid, T0 + timedelta(days=1)) == 0
        assert _state(conn, fid)[2] == T0


def test_provenance_refreshes_hash_immediately_but_last_seen_at_is_throttled(pg_database):
    with connection(pg_database, 'service_role') as conn:
        conn.autocommit = True
        fid = _feed(conn)
        _ingest(conn, fid)
        article = _state(conn, fid)[0]
        first = _provenance(conn, article)
        for _ in range(5):
            _ingest(conn, fid)
        assert _provenance(conn, article) == first

        # Older than the 6-hour bucket: the next sighting records freshness.
        with conn.cursor() as cur:
            cur.execute("UPDATE driftread.article_discoveries SET last_seen_at=now()-interval '7 hours' WHERE article_id=%s",
                        (article,))
        stale = _provenance(conn, article)
        _ingest(conn, fid)
        refreshed = _provenance(conn, article)
        assert refreshed[2] > stale[2]
        assert refreshed[2] > datetime.now(timezone.utc) - timedelta(minutes=5)

        _ingest(conn, fid, content='revised', content_hash='c' * 64)
        assert _provenance(conn, article)[1] == 'c' * 64
        # The per-source row stays unique; nothing is duplicated by throttling.
        assert scalar(conn, 'SELECT count(*) FROM driftread.article_discoveries WHERE article_id=%s', (article,)) == 1
