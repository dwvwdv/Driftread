"""Real PostgreSQL regression, including independent-connection lock races.

CI provides a local PostgreSQL service. Each module run creates its own database;
it never resets an existing application's tables or uses DATABASE_URL as input.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn
import pytest


@pytest.fixture(scope="module")
def pg_database():
    dsn = os.getenv("DRIFTREAD_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("DRIFTREAD_TEST_DATABASE_URL not set; real PostgreSQL tests run in CI")
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    if admin.info.host not in {"localhost", "127.0.0.1", "::1"} or not admin.info.dbname.endswith("_test"):
        admin.close()
        pytest.fail("PostgreSQL integration tests require a local *_test database")
    name = f"driftread_integration_{uuid4().hex}_test"
    test_dsn = make_dsn(dsn, dbname=name)
    with admin.cursor() as cur:
        for role in ("anon", "authenticated", "service_role", "authenticator"):
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if not cur.fetchone():
                suffix = sql.SQL(" BYPASSRLS") if role == "service_role" else sql.SQL("")
                cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)) + suffix)
        cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    try:
        with psycopg2.connect(test_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE SCHEMA auth;
                    CREATE TABLE auth.users(id uuid PRIMARY KEY);
                    CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
                     $$ SELECT coalesce(nullif(current_setting('request.jwt.claims',true),'')::jsonb,'{}'::jsonb) $$;
                    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
                     $$ SELECT (auth.jwt()->>'sub')::uuid $$;
                    GRANT USAGE ON SCHEMA auth TO anon,authenticated,service_role;
                    INSERT INTO auth.users VALUES(gen_random_uuid());""")
        conn.close()
        # Use the real ledger/transaction runner, not a list of rewritten DDL.
        from migrate import run_migrations
        original = os.environ.get("DATABASE_URL")
        try:
            os.environ["DATABASE_URL"] = test_dsn
            run_migrations()
            run_migrations()  # ledger-backed rerun must also be safe
        finally:
            if original is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original
        yield test_dsn
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


@contextmanager
def connection(dsn, role=None):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='5s'")
            if role:
                assert role in {"service_role", "authenticated", "anon"}
                cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        conn.commit()
        yield conn
    finally:
        conn.rollback()
        conn.close()


def scalar(conn, statement, params=()):
    with conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchone()[0]


def compact(conn):
    return scalar(conn, "SELECT driftread.compact_article_content(30,200,false)")


def seed_article(conn):
    feed = scalar(conn, "INSERT INTO driftread.feeds(title,url) VALUES('race',%s) RETURNING id",
                  (f"https://{uuid4().hex}.example/rss",))
    url = f"https://{uuid4().hex}.example/post"
    article = scalar(conn, """INSERT INTO driftread.articles
      (feed_id,title,url,content,content_hash,discovery_extracted_hash,discovery_extracted_at,fetched_at,content_revision_at)
      VALUES(%s,'original',%s,'original body',%s,%s,now(),now()-interval '60 days',now()-interval '60 days') RETURNING id""",
      (feed, url, "a" * 64, "a" * 64))
    user = scalar(conn, "SELECT id FROM auth.users LIMIT 1")
    conn.commit()
    return feed, article, url, user


def ingest(conn, feed, url, body, digest):
    payload = json.dumps([{"title": "original", "url": url, "content": body, "content_hash": digest}])
    return scalar(conn, "SELECT driftread.ingest_article_batch(%s,%s::jsonb)", (feed, payload))


def assert_blocked(observer, pid, future):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if future.done():
            future.result()  # surface an unexpected SQL error
            pytest.fail("write completed before the protecting transaction committed")
        if scalar(observer, "SELECT cardinality(pg_blocking_pids(%s))>0", (pid,)):
            return
        time.sleep(0.01)
    pytest.fail("write did not reach its expected database lock")


def test_complete_migration_chain_and_sql_fixtures(pg_database):
    with connection(pg_database) as conn:
        files = list((Path(__file__).parents[1] / "migrations").glob("*.sql"))
        assert scalar(conn, "SELECT count(*) FROM driftread._migrations") == len(files)
        conn.commit()
        conn.autocommit = True
        for name in ("test_crawler_lifecycle.sql", "test_global_settings.sql"):
            with conn.cursor() as cur:
                cur.execute((Path(__file__).parent / "sql" / name).read_text())


@pytest.mark.parametrize("kind", ["bookmark", "read", "subscription"])
def test_uncommitted_user_reference_prevents_compaction(pg_database, kind):
    with connection(pg_database) as setup, connection(pg_database) as user_conn, connection(pg_database, "service_role") as maintenance:
        feed, article, _, user = seed_article(setup)
        with user_conn.cursor() as cur:
            if kind == "bookmark":
                cur.execute("INSERT INTO driftread.user_bookmarks VALUES(%s,%s,'favorite',now())", (user, article))
            elif kind == "read":
                cur.execute("INSERT INTO driftread.user_article_reads VALUES(%s,%s,now())", (user, article))
            else:
                cur.execute("INSERT INTO driftread.user_feeds(user_id,feed_id) VALUES(%s,%s)", (user, feed))
        # FK KEY SHARE is still held by an independent, uncommitted session.
        assert compact(maintenance)["compacted"] == 0
        maintenance.commit()
        user_conn.commit()
        assert compact(maintenance)["compacted"] == 0
        assert scalar(setup, "SELECT content FROM driftread.articles WHERE id=%s", (article,)) == "original body"


def test_revision_and_completion_cas_race(pg_database):
    with connection(pg_database) as writer, connection(pg_database) as extractor, connection(pg_database) as observer:
        feed, article, url, _ = seed_article(writer)
        assert ingest(writer, feed, url, "new body", "b" * 64) == 1
        pid = extractor.get_backend_pid()
        def finish_old_version():
            with extractor.cursor() as cur:
                cur.execute("UPDATE driftread.articles SET discovery_extracted_hash=%s,discovery_extracted_at=now() WHERE id=%s AND content_hash=%s", ("a" * 64, article, "a" * 64))
                count = cur.rowcount
            extractor.commit()
            return count
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(finish_old_version)
            try:
                assert_blocked(observer, pid, future)
            finally:
                writer.commit()
            assert future.result(timeout=5) == 0
        assert scalar(observer, "SELECT discovery_extracted_at IS NULL FROM driftread.articles WHERE id=%s", (article,))


def test_inflight_revision_survives_compaction(pg_database):
    with connection(pg_database) as writer, connection(pg_database, "service_role") as maintenance:
        feed, article, url, _ = seed_article(writer)
        ingest(writer, feed, url, "new revision", "b" * 64)
        assert compact(maintenance)["compacted"] == 0
        maintenance.commit()
        writer.commit()
        with writer.cursor() as cur:
            cur.execute("UPDATE driftread.articles SET discovery_extracted_hash=content_hash,discovery_extracted_at=now() WHERE id=%s", (article,))
        writer.commit()
        assert compact(maintenance)["compacted"] == 0  # fresh version's own retention period
        assert scalar(writer, "SELECT content FROM driftread.articles WHERE id=%s", (article,)) == "new revision"


def test_compaction_wins_before_same_hash_refresh(pg_database):
    with connection(pg_database) as maintenance, connection(pg_database) as writer, connection(pg_database) as observer:
        feed, article, url, _ = seed_article(maintenance)
        with maintenance.cursor() as cur:
            cur.execute("SELECT id FROM driftread.feeds WHERE id=%s FOR UPDATE", (feed,))
            cur.execute("SELECT id FROM driftread.articles WHERE id=%s FOR UPDATE", (article,))
        pid = writer.get_backend_pid()
        def refresh():
            touched = ingest(writer, feed, url, "original body", "a" * 64)
            writer.commit()
            return touched
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(refresh)
            try:
                assert_blocked(observer, pid, future)
                assert compact(maintenance)["compacted"] == 1
            finally:
                maintenance.commit()
            assert future.result(timeout=5) == 0
        assert scalar(observer, "SELECT content IS NULL FROM driftread.articles WHERE id=%s", (article,))


def test_private_rpc_and_owner_rls(pg_database):
    with connection(pg_database) as setup:
        _, article, _, owner = seed_article(setup)
        other = scalar(setup, "INSERT INTO auth.users VALUES(gen_random_uuid()) RETURNING id")
        with setup.cursor() as cur:
            cur.execute("INSERT INTO driftread.user_bookmarks VALUES(%s,%s,'favorite',now())", (owner, article))
        setup.commit()
    with connection(pg_database, "anon") as anonymous:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            compact(anonymous)
    with connection(pg_database, "authenticated") as caller:
        with caller.cursor() as cur:
            cur.execute("SELECT set_config('request.jwt.claims',%s,false)", (json.dumps({"sub": str(other), "is_anonymous": False}),))
        assert scalar(caller, "SELECT count(*) FROM driftread.user_bookmarks") == 0
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with caller.cursor() as cur:
                cur.execute("INSERT INTO driftread.user_bookmarks VALUES(%s,%s,'read_later',now())", (owner, article))
