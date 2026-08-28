from __future__ import annotations
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


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


class FeedArticle(ArticleSummary):
    """One row of GET /feeds/{feed_id}/articles — an ArticleSummary plus this
    caller's read/bookmark state (false for both when the caller is
    anonymous), as returned by the `list_feed_articles` DB function
    (migration 016)."""
    fetched_at: datetime
    is_read: bool = False
    is_bookmarked: bool = False


class PaginatedFeedArticles(BaseModel):
    items: list[FeedArticle]
    next_cursor: str | None = None


class ReadReceipt(BaseModel):
    article_id: UUID
    read_at: datetime


class PaginatedReads(BaseModel):
    items: list[ReadReceipt]
    next_cursor: str | None = None


class StreamArticle(BaseModel):
    """One row of GET /me/stream — an ArticleSummary plus which feed it came
    from and this caller's read state, as returned by the
    `list_reading_stream` DB function (migration 015)."""
    id: UUID
    feed_id: UUID
    feed_title: str
    title: str
    url: str
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime
    is_read: bool
    read_at: datetime | None = None


class PaginatedStream(BaseModel):
    items: list[StreamArticle]
    next_cursor: str | None = None


class FeedUnreadCount(BaseModel):
    feed_id: UUID
    feed_title: str
    unread_count: int


class UnreadSummary(BaseModel):
    total_unread: int
    feeds: list[FeedUnreadCount] = []


class MarkAllReadRequest(BaseModel):
    """See routers/me.py::mark_all_read for how the two scopes are chosen.

    `article_ids` caps at 500 — generous headroom over GET /me/stream's own
    page-size cap of 100 (the "current page" case), while still bounding a
    single request's upsert batch.
    """
    article_ids: list[UUID] | None = Field(default=None, max_length=500)
    feed_id: UUID | None = None
    before: datetime | None = None


class MarkAllReadResult(BaseModel):
    marked: int


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


class FeedRefreshResult(BaseModel):
    """Response for a single manual feed refresh.

    `inserted` keeps its original rows-touched meaning — the browser
    extension and any external scripts already read this field name.
    """
    inserted: int
    feed_id: UUID
    status: Literal["updated", "not_modified", "failed"]
    new_articles: int
    total_articles: int | None = None


class FeedHealthSummary(BaseModel):
    id: UUID
    title: str
    url: str
    health_score: int
    consecutive_failures: int
    last_failure_at: datetime | None = None
    last_failure_reason: str | None = None
    last_fetched_at: datetime | None = None


# ── Autonomous discovery (migration 006) ─────────────────────────────────────
# Note the response model for a discovered feed is FeedCandidate, not
# DiscoveryCandidate: that name is already the dataclass in
# services/feed_discovery.py, and services/discovery_probe.py imports both.

_Url = Annotated[str, StringConstraints(max_length=2048)]


class DiscoveryTarget(BaseModel):
    """One host (or feed URL) in the crawl frontier."""
    id: UUID
    url: str
    host: str
    source: str
    source_feed_id: UUID | None = None
    referring_feed_count: int = 0
    status: str
    attempts: int = 0
    feeds_found: int = 0
    next_probe_at: datetime | None = None
    last_probe_at: datetime | None = None
    last_failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class PaginatedDiscoveryTargets(BaseModel):
    items: list[DiscoveryTarget]
    total: int
    page: int
    page_size: int


class FeedCandidate(BaseModel):
    """A feed URL the probe fetched and parsed, awaiting review."""
    id: UUID
    target_id: UUID | None = None
    feed_url: str
    title: str | None = None
    website_url: str | None = None
    source_host: str | None = None
    referring_feed_count: int = 0
    status: str
    feed_id: UUID | None = None
    review_note: str | None = None
    discovered_at: datetime
    last_seen_at: datetime | None = None
    reviewed_at: datetime | None = None


class PaginatedFeedCandidates(BaseModel):
    items: list[FeedCandidate]
    total: int
    page: int
    page_size: int


class DiscoverySource(BaseModel):
    id: UUID
    url: str
    kind: str
    label: str | None = None
    enabled: bool = True
    interval_hours: int = 168
    next_harvest_at: datetime | None = None
    last_harvested_at: datetime | None = None
    attempts: int = 0
    last_failure_reason: str | None = None
    targets_created: int = 0
    created_at: datetime
    updated_at: datetime


class SeedTargetsRequest(BaseModel):
    # 500 URLs at 2048 chars is ~1 MiB, comfortably under main.py's 6 MiB body cap.
    urls: list[_Url] = Field(min_length=1, max_length=500)


class SeedTargetsResult(BaseModel):
    accepted: int
    requeued: int
    skipped: int
    # Per-URL failures are reported rather than failing the whole batch, the same
    # shape routers/opml.py::import_opml uses for its `failed` list.
    rejected: list[str] = []


class DiscoverySourceInput(BaseModel):
    url: _Url
    kind: str = Field(default="links_page", pattern="^(links_page|opml)$")
    label: str | None = Field(default=None, max_length=200)


class AddSourcesRequest(BaseModel):
    items: list[DiscoverySourceInput] = Field(min_length=1, max_length=100)


class UpdateSourceRequest(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=8760)


class ApproveCandidateRequest(BaseModel):
    category: str | None = Field(default=None, max_length=100)
    tags: list[Annotated[str, StringConstraints(max_length=50)]] = Field(
        default=[], max_length=20
    )


class HoldCandidateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class RejectCandidateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    # Also mark the parent target rejected, so the host never re-enters the
    # frontier from any source.
    block_host: bool = False


class HarvestSummary(BaseModel):
    processed: int = 0
    articles_scanned: int = 0
    anchors_seen: int = 0
    hosts_kept: int = 0
    targets_created: int = 0
    referrers_recorded: int = 0
    failed: int = 0


class DirectorySummary(BaseModel):
    processed: int = 0
    targets_created: int = 0
    feed_targets_created: int = 0
    failed: int = 0


class ProbeSummary(BaseModel):
    processed: int = 0
    found: int = 0
    # `none_found`, not `none` — reads far better than `none` next to `found`.
    none_found: int = 0
    blocked: int = 0
    failed: int = 0
    exhausted: int = 0
    candidates_new: int = 0


class DiscoveryCycleSummary(BaseModel):
    directory: DirectorySummary
    harvest: HarvestSummary
    probe: ProbeSummary
    auto_promoted: int = 0
    imported: int = 0


class DiscoveryStats(BaseModel):
    targets_pending: int = 0
    targets_done: int = 0
    targets_blocked: int = 0
    targets_exhausted: int = 0
    targets_rejected: int = 0
    candidates_pending: int = 0
    candidates_held: int = 0
    candidates_approved: int = 0
    candidates_rejected: int = 0
    candidates_imported: int = 0
    sources_enabled: int = 0
