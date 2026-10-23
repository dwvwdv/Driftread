export interface Feed {
  id: string;
  title: string;
  url: string;
  description: string | null;
  website_url: string | null;
  language: string | null;
  category: string | null;
  tags: string[];
  article_count: number;
  last_fetched_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  first_party?: boolean;
  participation_mode?: 'normal' | 'signal_only' | 'private';
  signal_group?: string | null;
  fulltext_policy?: 'rss' | 'summary_only';
  /** Admin responses only: per-source override of the article.cap setting. */
  article_cap?: number | null;
  last_fetch_at?: string | null;
  last_ok_at?: string | null;
  admitted_at?: string | null;
  next_fetch_at?: string | null;
  last_failure_reason?: string | null;
  last_fetch_result?: {
    outcome: string;
    at: string;
    entries_seen?: number;
    missing_url?: number;
    duplicate_entries?: number;
    upserted?: number;
    new_articles?: number;
    total_articles?: number;
    http_status?: number;
  } | null;
}

export interface ArticleSummary {
  id: string;
  feed_id: string;
  title: string;
  url: string;
  summary: string | null;
  author: string | null;
  published_at: string | null;
}

export interface Article extends ArticleSummary {
  content: string | null;
  fetched_at: string;
  content_compacted_at?: string | null;
}

export interface FeedWithArticles extends Feed {
  articles: ArticleSummary[];
}

/** One row of GET /me/feeds — a Feed plus this caller's own display name for
 * it (TODO.md「支援每個來源的使用者自訂名稱」), null when none is set. Set via
 * PATCH /me/feeds/{feed_id}; scoped to this (user, feed) pair, not written
 * back to Feed.title, which every subscriber and the public catalog share. */
export interface SubscribedFeed extends Feed {
  custom_title: string | null;
  /** Non-null when this caller muted the subscription (TODO.md「支援來源靜音／
   * 暫停」) — excluded from 我的閱讀's stream and unread counts, but still
   * listed and manageable here. Set via PATCH /me/feeds/{feed_id}. */
  muted_at: string | null;
}

export interface PaginatedFeeds {
  items: Feed[];
  total: number;
  page: number;
  page_size: number;
}

// ── Feed article list (GET /feeds/{feed_id}/articles) ──────────────────────

/** One row of a feed's full article list — an ArticleSummary plus this
 * caller's read/bookmark state (false for both when signed out). */
export interface FeedArticle extends ArticleSummary {
  fetched_at: string;
  is_read: boolean;
  is_bookmarked: boolean;
}

export interface PaginatedFeedArticles {
  items: FeedArticle[];
  next_cursor: string | null;
}

// ── Full-text search (GET /search/articles, GET /search/feeds) ────────────

/** One row of GET /search/articles — an ArticleSummary plus which feed it
 * came from, a highlighted match snippet, this caller's read/bookmark state
 * (false for both when signed out) and its relevance rank. */
export interface ArticleSearchResult extends ArticleSummary {
  feed_title: string;
  snippet: string | null;
  fetched_at: string;
  is_read: boolean;
  is_bookmarked: boolean;
  rank: number;
}

export interface PaginatedArticleSearchResults {
  items: ArticleSearchResult[];
  next_cursor: string | null;
}

/** One row of GET /search/feeds — matched on name/description, kept separate
 * from article search results per that endpoint's own "分開呈現" design. */
export interface FeedSearchResult {
  id: string;
  title: string;
  url: string;
  description: string | null;
  snippet: string | null;
  website_url: string | null;
  language: string | null;
  category: string | null;
  tags: string[];
  article_count: number;
  created_at: string;
  rank: number;
}

export interface PaginatedFeedSearchResults {
  items: FeedSearchResult[];
  next_cursor: string | null;
}

export interface ReadReceipt {
  article_id: string;
  read_at: string;
}

export interface PaginatedReads {
  items: ReadReceipt[];
  next_cursor: string | null;
}

// ── Reading stream (GET /me/stream) ────────────────────────────────────────

/** One row of the aggregated article timeline across every subscribed feed. */
export interface StreamArticle {
  id: string;
  feed_id: string;
  feed_title: string;
  title: string;
  url: string;
  summary: string | null;
  author: string | null;
  published_at: string | null;
  fetched_at: string;
  is_read: boolean;
  read_at: string | null;
  why?: string | null;
  heat?: number;
  participant_count?: number;
  group_key?: string;
}

export interface PaginatedStream {
  items: StreamArticle[];
  next_cursor: string | null;
}

export interface PersonalHeatSnapshot extends PaginatedStream {
  snapshot_at: string;
  complete: boolean;
  behind_participant_count: number;
  candidate_limit: number;
}

export interface FeedUnreadCount {
  feed_id: string;
  feed_title: string;
  unread_count: number;
}

export interface UnreadSummary {
  total_unread: number;
  feeds: FeedUnreadCount[];
}

/** Body of POST /me/reads/mark-all — see routers/me.py::mark_all_read for
 * how the two scopes (explicit ids vs. server-evaluated filter) are chosen. */
export interface MarkAllReadRequest {
  article_ids?: string[];
  feed_id?: string;
  before?: string;
}

export interface MarkAllReadResult {
  marked: number;
}

export interface DiscoveredFeed {
  feed_url: string;
  title: string | null;
  website_url: string | null;
  already_exists: boolean;
  existing_feed_id: string | null;
}

export interface DiscoverResponse {
  source_url: string;
  candidates: DiscoveredFeed[];
}

export type BookmarkType = 'favorite' | 'read_later';

/** One row of GET /recommendations — a Feed plus a short explanation of why
 * it was picked (TODO.md 推薦回饋持久化 "顯示推薦理由"), null when nothing
 * about the caller's signals drove the pick. */
export interface RecommendedFeed {
  feed: Feed;
  reason: string | null;
}

export type FeedFeedbackType = 'liked' | 'disliked' | 'skipped';

/** One row of GET /me/feed-feedback — persisted 猜你喜歡 feedback, so it
 * survives across devices instead of living only in localStorage. */
export interface FeedFeedback {
  feed_id: string;
  feedback_type: FeedFeedbackType;
  created_at: string;
}

export interface UserPreferences {
  preferred_categories: string[];
  preferred_languages: string[];
}

export interface OpmlImportResult {
  imported: number;
  subscribed: number;
  failed: string[];
}

// ── Admin feed operations ──────────────────────────────────────────────────

/**
 * Health fields live here rather than on Feed because the public GET /feeds
 * response does not expose them — they only come back from
 * GET /admin/feeds/unhealthy.
 */
export interface FeedHealthSummary {
  id: string;
  title: string;
  url: string;
  health_score: number;
  consecutive_failures: number;
  last_failure_at: string | null;
  last_failure_reason: string | null;
  last_fetched_at: string | null;
}

/** Outcome counts from POST /admin/feeds/refresh-due. */
export interface RefreshDueSummary {
  processed: number;
  updated: number;
  not_modified: number;
  failed: number;
  archived: number;
  new_articles: number;
}

/** Result of refreshing a single feed via POST /admin/feeds/{id}/refresh. */
export interface RefreshFeedResult {
  inserted: number;
  feed_id: string;
  status: string;
  new_articles: number;
  total_articles: number;
}

// ── Autonomous discovery (admin only) ──────────────────────────────────────
// `title` and `website_url` on a candidate are third-party text scraped from a
// remote feed. Render them with interpolation only — never [innerHTML] — and
// always show `source_host` (which we normalized ourselves) alongside, so a
// spoofed title can't mislead whoever is approving it.

export interface FeedCandidate {
  id: string;
  target_id: string | null;
  feed_url: string;
  title: string | null;
  website_url: string | null;
  source_host: string | null;
  referring_feed_count: number;
  status: 'pending' | 'held' | 'approved' | 'rejected' | 'imported';
  feed_id: string | null;
  review_note: string | null;
  manual_override?: 'approve' | 'reject' | 'hold' | null;
  discovered_at: string;
  last_seen_at: string | null;
  reviewed_at: string | null;
}

export interface PaginatedFeedCandidates {
  items: FeedCandidate[];
  total: number;
  page: number;
  page_size: number;
}

/** Admission provenance records the action that created the source, not its latest AI result. */
export interface RecentAdmission {
  feed_id: string;
  title: string | null;
  feed_url: string;
  website_url: string | null;
  admitted_at: string;
  method: 'ai_auto' | 'human_agree' | 'human_disagree' | 'manual' | 'referrer_auto' | 'unknown';
  engine?: 'external_agent' | 'jev' | null;
  candidate_id?: string | null;
  review_id?: string | null;
}

export interface DiscoveryTarget {
  id: string;
  url: string;
  host: string;
  source: 'article_link' | 'blogroll' | 'seed' | 'directory' | 'opml';
  source_feed_id: string | null;
  referring_feed_count: number;
  status: 'pending' | 'done' | 'blocked' | 'exhausted' | 'rejected' | 'removed';
  attempts: number;
  feeds_found: number;
  next_probe_at: string | null;
  last_probe_at: string | null;
  last_failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedDiscoveryTargets {
  items: DiscoveryTarget[];
  total: number;
  page: number;
  page_size: number;
}

export interface DiscoverySource {
  id: string;
  url: string;
  kind: 'links_page' | 'opml';
  label: string | null;
  enabled: boolean;
  interval_hours: number;
  next_harvest_at: string | null;
  last_harvested_at: string | null;
  attempts: number;
  last_failure_reason: string | null;
  targets_created: number;
  created_at: string;
  updated_at: string;
}

export interface SeedTargetsResult {
  accepted: number;
  requeued: number;
  skipped: number;
  rejected: string[];
}

export interface DiscoveryStats {
  targets_pending: number;
  targets_done: number;
  targets_blocked: number;
  targets_exhausted: number;
  targets_rejected: number;
  candidates_pending: number;
  candidates_held: number;
  candidates_approved: number;
  candidates_rejected: number;
  candidates_imported: number;
  sources_enabled: number;
}

export interface DiscoveryCycleSummary {
  paused?: boolean;
  directory: Record<string, number>;
  harvest: Record<string, number>;
  probe: Record<string, number>;
  auto_promoted: number;
  imported: number;
}

export interface WorkerHeartbeat {
  worker_id: string;
  hostname: string;
  heartbeat_at: string;
  status: string;
  age_seconds: number;
  stale: boolean;
}

export interface WorkerRun {
  id: string;
  worker_id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
  error: string | null;
}

/** The scheduler configuration a worker actually parsed from its own env. */
export interface WorkerSchedulerConfig {
  refresh_enabled: boolean;
  discovery_deployment_enabled: boolean;
  retention: {
    enabled: boolean;
    mode: 'apply' | 'disabled';
    days: number;
    batch_size: number;
    interval_minutes: number;
  };
}

export interface ReportedWorkerConfig {
  worker_id: string;
  hostname: string | null;
  reported_at: string;
  stale: boolean;
  status: string;
  config: WorkerSchedulerConfig;
}

export interface OperationsStatus {
  observed_at: string;
  stale_after_seconds: number;
  retention_days: number;
  workers: WorkerHeartbeat[];
  recent_runs: WorkerRun[];
  recent_failures: number;
  /** Absent on an older API; null per kind when no run is on record. */
  latest_runs?: Record<string, WorkerRun | null>;
  /** null when no heartbeat carries a config (older worker image). */
  worker_config?: ReportedWorkerConfig | null;
  queue?: {
    queued: number;
    running: number;
    dead: number;
    succeeded: number;
    next_available_at: string | null;
  };
  alerts?: {
    id: string;
    worker_id: string;
    kind: string;
    created_at: string;
    resolved_at: string | null;
  }[];
}

export interface ArticleStorageStats {
  article_count: number;
  full_content_count: number;
  summary_only_count: number;
  pending_discovery_count: number;
  compacted_count: number;
  table_bytes: number;
  index_bytes: number;
  total_bytes: number;
  database_bytes: number;
}

export interface StoragePoint extends ArticleStorageStats {
  snapshot_date: string;
  observed_at: string;
}

export interface StorageHistory {
  observed_at: string;
  retention_days: number;
  points: StoragePoint[];
  growth: {
    start_at: string;
    end_at: string;
    elapsed_days: number;
    article_count_per_day: number;
    table_bytes_per_day: number;
    index_bytes_per_day: number;
    total_bytes_per_day: number;
    database_bytes_per_day: number;
  } | null;
}

export interface TableStat {
  name: string;
  heap_bytes: number;
  toast_bytes: number;
  index_bytes: number;
  total_bytes: number;
  n_live_tup: number | null;
  n_dead_tup: number | null;
  n_tup_ins: number | null;
  n_tup_upd: number | null;
  n_tup_del: number | null;
  n_tup_hot_upd: number | null;
  last_vacuum: string | null;
  last_autovacuum: string | null;
  vacuum_count: number | null;
  autovacuum_count: number | null;
  dead_ratio: number | null;
}

export interface TableDelta {
  name: string;
  /** false: statistics were reset or the table is new; counters are unknown. */
  comparable: boolean;
  inserted: number | null;
  updated: number | null;
  deleted: number | null;
  hot_updated: number | null;
  total_bytes_delta: number | null;
  n_dead_tup: number | null;
}

export interface TableInterval {
  start_at: string;
  end_at: string;
  elapsed_hours: number;
  stats_reset_changed: boolean;
  tables: TableDelta[];
}

export interface TableStorageHistory {
  observed_at: string;
  retention_days: number;
  current: { observed_at: string; stats_reset: string | null; tables: TableStat[] };
  /** Newest first. */
  intervals: TableInterval[];
}

export type SourceActivityAlert = 'new_articles' | 'revision_updates' | 'revisions_per_article';

export interface SourceActivity {
  feed_id: string;
  title: string | null;
  url: string;
  archived: boolean;
  new_articles: number;
  backfill_articles: number;
  revision_updates: number;
  revised_articles: number;
  content_changes: number;
  metadata_only_changes: number;
  revisions_per_article: number | null;
  alerts: SourceActivityAlert[];
}

export interface IngestActivity {
  observed_at: string;
  since: string;
  window_hours: number;
  thresholds: {
    new_articles: number;
    revision_updates: number;
    revisions_per_article: number;
    revisions_per_article_min_updates: number;
  };
  top_ingest: SourceActivity[];
  top_revisions: SourceActivity[];
  /** Over-threshold sources across the whole window (up to `limit`), not only the top lists. */
  flagged: SourceActivity[];
  flagged_sources: number;
}

export interface DiscoveryProfile {
  id: string;
  name: string;
  language: string;
  category: string | null;
  enabled: boolean;
  quota: number;
  seed_urls: string[];
}

export interface DiscoveryProfilesValue {
  profiles: DiscoveryProfile[];
}

export interface GlobalSetting<T = unknown> {
  key: string;
  value: T;
  version: number;
  updated_at: string | null;
}

export interface GlobalSettingsResponse {
  settings: GlobalSetting[];
}

export interface SourceReviewSettings {
  enabled: boolean;
  skip_human_review: boolean;
  engine: 'external_agent' | 'jev';
  model: string;
  min_confidence: number;
  min_quality: number;
  max_spam_probability: number;
  policy_text: string;
  daily_budget_usd: number;
  sample_size: number;
}

/** Global `article.cap` setting; sources may override max_per_feed. */
export interface ArticleCapSettings {
  enabled: boolean;
  max_per_feed: number;
}

export interface ArticleCapSummary {
  dry_run: boolean;
  feeds_over_cap: number;
  /** Cached count minus cap, protected articles included: an upper bound. */
  excess: number;
  eligible: number;
  deleted: number;
  skipped_feeds: number;
}

export interface SourceReviewResult {
  decision: 'accept' | 'reject' | 'abstain';
  confidence: number;
  dimensions: { quality: number; spam_probability: number; relevance: number };
  reason_codes: string[];
  rationale?: string | null;
}

export interface SourceReview {
  id: string;
  candidate_id: string;
  status:
    | 'requested'
    | 'preparing'
    | 'ready'
    | 'leased'
    | 'awaiting_human'
    | 'accepted'
    | 'rejected'
    | 'deferred'
    | 'failed'
    | 'stale'
    | 'overridden';
  engine: 'external_agent' | 'jev';
  model?: string;
  policy_version?: number;
  request_action?: 'enqueued' | 'requeued' | 'reused';
  error_code?: string | null;
  result?: SourceReviewResult | null;
  manual_override?: 'approve' | 'reject' | 'hold' | null;
  human_review?: {
    assessment: 'agree' | 'disagree';
    ai_decision: 'accept' | 'reject' | 'abstain';
    decision: 'accept' | 'reject' | 'defer';
    decided_at: string;
  } | null;
  created_at?: string;
  updated_at?: string;
}

/** Each source-review batch commits independently and reports per-item failures. */
export interface SourceReviewBatchResult {
  items: ({ candidate_id?: string; review_id?: string } & (
    | { ok: true; review: SourceReview }
    | { ok: false; status_code: number; detail: unknown }
  ))[];
  succeeded: number;
  failed: number;
}
