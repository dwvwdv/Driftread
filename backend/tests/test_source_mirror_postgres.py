from uuid import uuid4

import psycopg2
import pytest

from tests.test_postgres_lifecycle import pg_database, connection, scalar  # noqa: F401


def _feed(cur, mode="normal", archived=False):
    cur.execute(
        """INSERT INTO driftread.feeds(title,url,participation_mode,archived_at)
           VALUES('f',%s,%s,CASE WHEN %s THEN now() END) RETURNING id""",
        (f"https://{uuid4().hex}.example/rss", mode, archived),
    )
    return cur.fetchone()[0]


def _articles(cur, feed, urls):
    for i, url in enumerate(urls):
        cur.execute(
            """INSERT INTO driftread.articles(feed_id,title,url,published_at)
               VALUES(%s,'a',%s,now() - %s * interval '1 hour')""",
            (feed, url, i),
        )


def _sources(conn, urls, exclude=None):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT feed_id::text, overlap, recent_overlap FROM driftread.find_mirror_sources(%s::text[], %s::uuid)",
            (urls, exclude),
        )
        return cur.fetchall()


def test_mirror_sources_measure_both_directions(pg_database):
    site = f"https://{uuid4().hex}.example"
    tech = [f"{site}/tech/{i}" for i in range(10)]
    other = [f"{site}/news/{i}" for i in range(30)]
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            main = _feed(cur)
            # Main feed: newest 30 are news, older 10 are tech.
            _articles(cur, main, other + tech)
            mirror = _feed(cur)
            _articles(cur, mirror, [f"{u}#comment-1" for u in other[:20]])
            archived = _feed(cur, archived=True)
            _articles(cur, archived, other)
            private = _feed(cur, mode="private")
            _articles(cur, private, other)
        conn.commit()

    with connection(pg_database, "service_role") as conn:
        # A true mirror: every item stored and the main feed's newest match.
        rows = _sources(conn, other[:20], exclude=str(mirror))
        assert rows[0] == (str(main), 20, 20)
        # Canonical URLs drop fragments, so the comment-fragment copy counts.
        assert (str(mirror), 20, 20) in _sources(conn, other[:20], exclude=str(main))
        # A category feed: fully contained, but not the main feed's newest items.
        assert _sources(conn, tech) == [(str(main), 10, 0)]
        # Archived and private sources are never mirror targets.
        found = {row[0] for row in _sources(conn, other)}
        assert str(archived) not in found and str(private) not in found
        assert _sources(conn, [f"{site}/unknown"]) == []


def test_recent_window_follows_timeline_not_claimed_dates(pg_database):
    """Undated recent items fall back to their discovery time, so they are
    the newest; an old claimed date cannot push them out of the window."""
    site = f"https://{uuid4().hex}.example"
    recent = [f"{site}/new/{i}" for i in range(10)]
    with connection(pg_database) as conn:
        with conn.cursor() as cur:
            feed = _feed(cur)
            for i in range(10):
                cur.execute(
                    "INSERT INTO driftread.articles(feed_id,title,url,published_at) VALUES(%s,'old',%s,'2020-01-01')",
                    (feed, f"{site}/old/{i}"),
                )
            for url in recent:
                cur.execute("INSERT INTO driftread.articles(feed_id,title,url) VALUES(%s,'new',%s)", (feed, url))
        conn.commit()
    with connection(pg_database, "service_role") as conn:
        assert _sources(conn, recent) == [(str(feed), 10, 10)]


def test_mirror_sources_are_service_only(pg_database):
    for role in ("anon", "authenticated"):
        with connection(pg_database, role) as conn:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                _sources(conn, ["https://example.com/a"])
