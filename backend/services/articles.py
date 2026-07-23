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
    time"). Returns the number of rows upserted.
    """
    seen: set[str] = set()
    rows: list[dict] = []
    for article in articles:
        if not article.url or article.url in seen:
            continue
        seen.add(article.url)
        rows.append(
            {
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
        )

    inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        result = db.table("articles").upsert(chunk, on_conflict="url").execute()
        inserted += len(result.data)
    return inserted
