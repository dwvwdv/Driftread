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
}

export interface FeedWithArticles extends Feed {
  articles: ArticleSummary[];
}

export interface PaginatedFeeds {
  items: Feed[];
  total: number;
  page: number;
  page_size: number;
}

export interface PaginatedArticles {
  items: ArticleSummary[];
  total: number;
  page: number;
  page_size: number;
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

export interface DiscoveryTarget {
  id: string;
  url: string;
  host: string;
  source: 'article_link' | 'blogroll' | 'seed' | 'directory' | 'opml';
  source_feed_id: string | null;
  referring_feed_count: number;
  status: 'pending' | 'done' | 'blocked' | 'exhausted' | 'rejected';
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
  directory: Record<string, number>;
  harvest: Record<string, number>;
  probe: Record<string, number>;
  auto_promoted: number;
  imported: number;
}
