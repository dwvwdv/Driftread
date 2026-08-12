"""One-off data repairs that need the parser's own logic.

Separate from migrate.py because these are not schema changes and, critically,
not expressible in SQL. `_plain_text()` calls html.unescape(), which knows 2231
named entities, the Windows-1252 substitutions for the C1 range, the set of code
points whose reference is dropped outright, and single-pass scanning semantics.
Reimplementing that in PL/pgSQL was tried and produced a fresh divergence on
every review pass — a partial entity table, a hex branch that silently failed, a
C1 range stored as control characters, and a decode that re-scanned its own
output. Calling the parser is the only version that cannot drift from it.

Tracked in the same `driftread._migrations` table as the SQL files so "has this run?" has
one answer, and so an operator reading that table sees the whole sequence.
"""
from __future__ import annotations
import logging
import os

import psycopg2
import psycopg2.extras

from rss_parser import _looks_like_markup, _plain_text

logger = logging.getLogger(__name__)

# Recorded in `_migrations`. Named like the SQL files so the ordering reads
# correctly there; the .py suffix keeps migrate.py's glob from ever picking it up.
BACKFILL_NAME = "009_plain_text_article_summaries.py"

# Tuned for a table of cached articles rather than a warehouse: big enough that
# the round trips do not dominate, small enough that one batch's UPDATE stays
# short and memory stays flat.
BATCH_SIZE = 500


def _repair(summary: str | None, content: str | None) -> tuple[str | None, str | None]:
    """Return the (summary, content) the parser would store for this row today.

    Mirrors the branch in rss_parser's item loop: a markup summary on a row with
    no content *is* the body, and summary is always flattened text.
    """
    new_content = content
    if not (content or "").strip() and _looks_like_markup(summary):
        new_content = summary
    return _plain_text(summary), new_content


def backfill_plain_text_summaries(conn) -> int:
    """Rewrite `articles.summary` as plain text, rescuing bodies into `content`.

    The parser fix only reaches rows it upserts again; an article that has
    already scrolled out of its feed's window never will, so its markup would
    stay in a text-only field forever. Returns the number of rows changed.
    """
    changed = 0
    with conn.cursor(name="backfill_articles", cursor_factory=psycopg2.extras.DictCursor) as read:
        # Server-side cursor: the whole point is to walk every row, and this
        # table holds every article ever cached.
        read.itersize = BATCH_SIZE
        read.execute(
            "SELECT id, summary, content "
            "FROM driftread.articles WHERE summary IS NOT NULL"
        )

        updates: list[tuple[str | None, str | None, str]] = []
        with conn.cursor() as write:
            for row in read:
                summary, content = _repair(row["summary"], row["content"])
                if summary == row["summary"] and content == row["content"]:
                    continue
                updates.append((summary, content, row["id"]))

                if len(updates) >= BATCH_SIZE:
                    changed += _flush(write, updates)
                    updates.clear()

            changed += _flush(write, updates)
    return changed


def _flush(cur, updates: list[tuple[str | None, str | None, str]]) -> int:
    if not updates:
        return 0
    psycopg2.extras.execute_batch(
        cur,
        "UPDATE driftread.articles SET summary = %s, content = %s WHERE id = %s",
        updates,
        page_size=len(updates),
    )
    return len(updates)


def run_backfills() -> None:
    """Apply any data backfill that has not run yet. Safe to call on every boot.

    Mirrors run_migrations()'s contract exactly: a missing DATABASE_URL is a
    warning rather than a failure, so a deployment that only talks to Supabase
    over PostgREST still boots.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.warning("DATABASE_URL not set — skipping data backfill")
        return

    conn = psycopg2.connect(db_url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # run_migrations() creates the schema-scoped ledger first.
            cur.execute(
                "SELECT 1 FROM driftread._migrations WHERE filename = %s",
                (BACKFILL_NAME,),
            )
            if cur.fetchone():
                return
            conn.commit()

        logger.info("Running backfill: %s", BACKFILL_NAME)
        changed = backfill_plain_text_summaries(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO driftread._migrations (filename) VALUES (%s) "
                "ON CONFLICT (filename) DO NOTHING",
                (BACKFILL_NAME,),
            )
        conn.commit()
        logger.info("Backfill %s complete: %d rows rewritten", BACKFILL_NAME, changed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
