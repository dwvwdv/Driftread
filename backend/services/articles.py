"""Shared article-upsert logic used by feed import/refresh endpoints."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

    from rss_parser import ParsedArticle

CHUNK_SIZE = 200


def upsert_articles(db: "Client", feed_id: str, articles: list["ParsedArticle"]) -> int:
    """Upsert parsed articles for a feed, deduping by URL and batching in
    chunks of `CHUNK_SIZE` (a single request with duplicate URLs would make
    Postgres raise "ON CONFLICT DO UPDATE command cannot affect row a second
    time").

    Returns the number of rows *touched*, which includes updates to articles
    already stored — a feed serves the same recent items on every poll, so this
    is close to the feed's item count and says nothing about whether anything
    new arrived. Callers that need that (services/feed_refresh.py) compare
    article counts before and after instead.
    """
    # Keyed by URL so a repeated URL keeps the *last* occurrence's data,
    # matching the previous sequential-upsert behavior (each upsert
    # overwrote the row) while still sending one row per URL per batch.
    # One feed_id per call, so keying on URL alone matches the per-feed
    # uniqueness the articles_feed_id_url_key constraint enforces.
    rows_by_url: dict[str, dict] = {}
    for article in articles:
        if not article.url:
            continue
        rows_by_url[article.url] = {
            "feed_id": feed_id,
            "title": article.title,
            "url": article.url,
            "summary": article.summary,
            "content": article.content,
            "author": article.author,
            "published_at": article.published_at.isoformat()
            if article.published_at
            else None,
        }
    rows = list(rows_by_url.values())

    inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        # Must match articles_feed_id_url_key (migration 005). The old
        # on_conflict="url" targets a constraint that no longer exists.
        result = db.table("articles").upsert(chunk, on_conflict="feed_id,url").execute()
        inserted += len(result.data)
    return inserted
