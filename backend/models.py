from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Feed(BaseModel):
    id: UUID
    title: str
    url: str
    description: str | None = None
    website_url: str | None = None
    language: str | None = None
    category: str | None = None
    tags: list[str] = []
    article_count: int = 0
    last_fetched_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Scheduling / conditional-GET state (migration 005). Optional so rows read
    # before the migration lands still deserialize.
    fetch_interval_minutes: int | None = None
    next_fetch_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None


class FeedCreate(BaseModel):
    title: str
    url: str
    description: str | None = None
    website_url: str | None = None
    language: str | None = None
    category: str | None = None
    tags: list[str] = []


class FeedUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    website_url: str | None = None
    language: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class Article(BaseModel):
    id: UUID
    feed_id: UUID
    title: str
    url: str
    summary: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime


class ArticleSummary(BaseModel):
    id: UUID
    feed_id: UUID
    title: str
    url: str
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None


class FeedWithArticles(Feed):
    articles: list[ArticleSummary] = []


class ImportFeedsRequest(BaseModel):
    feeds: list[FeedCreate]


class RecommendationRequest(BaseModel):
    liked_feed_ids: list[str] = []
    disliked_feed_ids: list[str] = []
    limit: int = 10


class PaginatedFeeds(BaseModel):
    items: list[Feed]
    total: int
    page: int
    page_size: int


class PaginatedArticles(BaseModel):
    items: list[ArticleSummary]
    total: int
    page: int
    page_size: int


class BookmarkCreate(BaseModel):
    article_id: UUID
    bookmark_type: str  # 'favorite' | 'read_later'


class Bookmark(BaseModel):
    article_id: UUID
    bookmark_type: str
    created_at: datetime


class UserPreferences(BaseModel):
    """Response shape — unconstrained so rows saved before the 50-item cap
    (see UserPreferencesUpdate) don't turn GET /me/preferences into a 500."""
    preferred_categories: list[str] = []
    preferred_languages: list[str] = []


class UserPreferencesUpdate(BaseModel):
    preferred_categories: list[str] = Field(default=[], max_length=50)
    preferred_languages: list[str] = Field(default=[], max_length=50)


class DiscoverRequest(BaseModel):
    url: str = Field(max_length=2048)


class DiscoveredFeed(BaseModel):
    feed_url: str
    title: str | None = None
    website_url: str | None = None
    already_exists: bool = False
    existing_feed_id: UUID | None = None


class DiscoverResponse(BaseModel):
    source_url: str
    candidates: list[DiscoveredFeed]


class DiscoverImportRequest(BaseModel):
    feed_url: str = Field(max_length=2048)


class OpmlImportResult(BaseModel):
    imported: int
    subscribed: int
    failed: list[str] = []


class RefreshDueSummary(BaseModel):
    """Outcome counts for one pass over the due queue."""
    processed: int
    updated: int
    not_modified: int
    failed: int
    archived: int
    new_articles: int


class FeedHealthSummary(BaseModel):
    id: UUID
    title: str
    url: str
    health_score: int
    consecutive_failures: int
    last_failure_at: datetime | None = None
    last_failure_reason: str | None = None
    last_fetched_at: datetime | None = None
