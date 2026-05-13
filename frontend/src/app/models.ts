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
