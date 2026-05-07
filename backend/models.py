from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, HttpUrl


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
