"""Shared, hash-aware article ingestion for imports and refreshes."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from services.discovery_config import discovery_enabled
from services.link_harvest import (
    build_host_index,
    content_fingerprint,
    extract_document_hosts,
    harvest_pending_articles,
)

if TYPE_CHECKING:
    from supabase import Client
    from rss_parser import ParsedArticle

logger = logging.getLogger(__name__)
CHUNK_SIZE = 200


def upsert_articles(
    db: "Client", feed_id: str, articles: list["ParsedArticle"], *,
    origin: Literal["rss", "discover_import", "historical_import"] = "rss",
    backfill: bool = False, backfill_reason: str | None = None,
) -> int:
    """Store unique feed URLs atomically, returning rows actually changed.

    Parse links from incoming HTML before storage. Discovery failures never
    discard an article: unmarked rows remain in the incremental retry queue.
    The ingestion RPC skips identical rows and preserves compacted bodies when
    the source hash is unchanged. Deploy its migration before this code.
    """
    if origin not in {"rss", "discover_import", "historical_import"}:
        raise ValueError("invalid article discovery origin")
    if backfill_reason is not None and (not backfill or len(backfill_reason) > 100):
        raise ValueError("backfill_reason requires historical ingestion and at most 100 characters")
    rows_by_url: dict[str, dict] = {}
    for article in articles:
        if not article.url:
            continue
        rows_by_url[article.url] = {
            "feed_id": feed_id,
            "origin": origin,
            "backfill": backfill,
            "backfill_reason": backfill_reason,
            "title": article.title,
            "url": article.url,
            "summary": article.summary,
            "content": article.content,
            "content_hash": content_fingerprint(article.content, article.summary),
            "author": article.author,
            "published_at": article.published_at.isoformat() if article.published_at else None,
        }
    rows = list(rows_by_url.values())
    touched = 0
    index = None
    feed = None
    for offset in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[offset:offset + CHUNK_SIZE]
        # Pure local parsing; never fetch a linked page here, even when discovery
        # is enabled. Hash keys keep a concurrent source revision separate.
        prepared = {}
        enabled = discovery_enabled()
        if enabled:
            for row in chunk:
                try:
                    prepared[(row["url"], row["content_hash"])] = extract_document_hosts(
                        row["content"] or row["summary"] or "", row["url"]
                    )
                except Exception:
                    logger.exception("Deferred source parsing for feed %s", feed_id)
        response = db.rpc("ingest_article_batch", {
            "p_feed_id": feed_id, "p_articles": chunk,
        }).execute()
        touched += int(response.data or 0)
        if not enabled:
            continue
        try:
            # Unchanged input can still have a failed historical extraction.
            # Ask the indexed per-feed queue first; do not scan the global feed
            # and frontier catalogs when this feed has no work to extract.
            pending_rows = list(db.rpc("pending_article_discovery", {
                "p_feed_id": feed_id, "p_limit": CHUNK_SIZE,
            }).execute().data or [])
            if not pending_rows:
                continue
            if index is None:
                index = build_host_index(db)
                response = db.table("feeds").select("id,url,website_url").eq(
                    "id", feed_id
                ).limit(1).execute()
                feed = next(iter(response.data or []), {"id": feed_id})
            harvest_pending_articles(
                db, feed, index, CHUNK_SIZE, prepared=prepared, pending_rows=pending_rows
            )
        except Exception:
            # Article durability is independent of frontier availability. The
            # backlog worker retries unmarked versions, including zero-link HTML.
            logger.exception("Deferred article discovery for feed %s", feed_id)
    return touched
