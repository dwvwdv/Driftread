import { BookmarkType } from '../models';
import {
  OfflineAccountRecord,
  OfflineArticle,
  OfflineKind,
  PendingOperation,
} from './offline-store';

/**
 * Pure cache/queue rules for the PWA offline reader. Same semantics as the
 * extension's extension/offline.js and the /me/sync contract in
 * docs/CONSUMPTION_SURFACES.md「同步與離線快取的限制」.
 */

/** Matches the extension: summaries older than this are not shown, because
 * revocations only reach the device on the next sync. */
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
/** GET /me/sync accepts at most 100 pending_ids. */
export const PENDING_ARTICLE_LIMIT = 100;
export const OFFLINE_KINDS: readonly OfflineKind[] = ['read', 'favorite', 'read_later'];

/** GET /me/sync response (backend/routers/sync.py, personal_sync_snapshot). */
export interface SyncSnapshot {
  account_id: string;
  cursor: string;
  changed: boolean;
  reset?: boolean;
  items: Record<string, unknown>[] | null;
  authorized_article_ids: string[];
  cache_limit?: number;
}

export interface OfflineView {
  article: OfflineArticle;
  read: boolean;
  favorite: boolean;
  readLater: boolean;
  /** Kinds with a queued operation not yet acknowledged by the server. */
  pending: OfflineKind[];
}

export function emptyRecord(userId: string): OfflineAccountRecord {
  return { userId, items: [], pending: [], cursor: null, updatedAt: 0 };
}

export function isFresh(record: OfflineAccountRecord | null, now = Date.now()): boolean {
  return !!record && record.updatedAt > 0 && now - record.updatedAt < CACHE_TTL_MS;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** Keeps only the whitelisted summary fields; never `content`/`search_vector`. */
export function toOfflineArticle(raw: Record<string, unknown>): OfflineArticle | null {
  const id = text(raw['id']);
  if (!id) return null;
  const bookmarks = Array.isArray(raw['bookmark_types']) ? raw['bookmark_types'] : [];
  return {
    id,
    feed_id: text(raw['feed_id']) ?? '',
    feed_title: text(raw['feed_title']),
    title: text(raw['title']) ?? '',
    url: text(raw['url']) ?? '',
    summary: text(raw['summary']),
    author: text(raw['author']),
    published_at: text(raw['published_at']),
    timeline_at: text(raw['timeline_at']),
    is_read: raw['is_read'] === true,
    bookmark_types: bookmarks.filter(
      (b): b is BookmarkType => b === 'favorite' || b === 'read_later',
    ),
  };
}

/**
 * Applies one /me/sync response. `changed` replaces the whole bounded cache
 * (replacement snapshot, never merged); otherwise only the cursor advances.
 * Pending operations for articles we asked about and the server no longer
 * authorizes are dropped; ones queued after the request was built are kept.
 */
export function applySnapshot(
  record: OfflineAccountRecord,
  response: SyncSnapshot,
  requested: ReadonlySet<string>,
  now = Date.now(),
): OfflineAccountRecord {
  if (response.account_id !== record.userId) throw new Error('Account changed');
  const items = response.changed
    ? (response.items ?? []).map(toOfflineArticle).filter((a): a is OfflineArticle => a !== null)
    : record.items;
  const authorized = new Set(response.authorized_article_ids ?? []);
  const pending = record.pending.filter(
    (p) => !requested.has(p.articleId) || authorized.has(p.articleId),
  );
  return { ...record, items, pending, cursor: response.cursor, updatedAt: now };
}

/** Coalesces a desired state into the queue, like DriftreadOffline.queue. */
export function queueOperation(
  record: OfflineAccountRecord,
  articleId: string,
  kind: OfflineKind,
  enabled: boolean,
): OfflineAccountRecord {
  if (!OFFLINE_KINDS.includes(kind) || !record.items.some((a) => a.id === articleId)) {
    throw new Error('這篇文章不在離線快取中');
  }
  const articles = new Set(record.pending.map((p) => p.articleId));
  if (!articles.has(articleId) && articles.size >= PENDING_ARTICLE_LIMIT) {
    throw new Error(`待同步文章已達 ${PENDING_ARTICLE_LIMIT} 篇，請先連線同步`);
  }
  const pending = record.pending.filter((p) => !(p.articleId === articleId && p.kind === kind));
  pending.push({ articleId, kind, enabled });
  return { ...record, pending };
}

/** Removes exactly the replayed operation; a newer toggle stays queued. */
export function acknowledge(
  record: OfflineAccountRecord,
  operation: PendingOperation,
): OfflineAccountRecord {
  return {
    ...record,
    pending: record.pending.filter(
      (p) =>
        !(
          p.articleId === operation.articleId &&
          p.kind === operation.kind &&
          p.enabled === operation.enabled
        ),
    ),
  };
}

/** Snapshot state with queued operations laid over it (optimistic view). */
export function overlay(record: OfflineAccountRecord): OfflineView[] {
  const queued = new Map<string, PendingOperation[]>();
  for (const op of record.pending) {
    queued.set(op.articleId, [...(queued.get(op.articleId) ?? []), op]);
  }
  return record.items.map((article) => {
    const ops = queued.get(article.id) ?? [];
    const state = (kind: OfflineKind, base: boolean) =>
      ops.find((op) => op.kind === kind)?.enabled ?? base;
    return {
      article,
      read: state('read', article.is_read),
      favorite: state('favorite', article.bookmark_types.includes('favorite')),
      readLater: state('read_later', article.bookmark_types.includes('read_later')),
      pending: ops.map((op) => op.kind),
    };
  });
}
