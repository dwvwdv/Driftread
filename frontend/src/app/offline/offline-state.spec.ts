import {
  CACHE_TTL_MS,
  PENDING_ARTICLE_LIMIT,
  SyncSnapshot,
  acknowledge,
  applySnapshot,
  emptyRecord,
  isFresh,
  overlay,
  queueOperation,
  toOfflineArticle,
} from './offline-state';

const raw = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  feed_id: 'f1',
  feed_title: 'Feed',
  title: `T ${id}`,
  url: `https://example.com/${id}`,
  summary: `<p>${id}</p>`,
  author: null,
  published_at: '2026-10-01T00:00:00Z',
  timeline_at: '2026-10-01T00:00:00Z',
  is_read: false,
  bookmark_types: [],
  ...extra,
});

const snapshot = (overrides: Partial<SyncSnapshot> = {}): SyncSnapshot => ({
  account_id: 'user-a',
  cursor: 'c1',
  changed: true,
  items: [raw('a1'), raw('a2')],
  authorized_article_ids: [],
  ...overrides,
});

describe('offline-state', () => {
  it('keeps only whitelisted summary fields, never content or search_vector', () => {
    const article = toOfflineArticle(
      raw('a1', {
        content: '<p>body</p>',
        search_vector: "'x':1",
        bookmark_types: ['favorite', 'evil'],
      }),
    )!;
    expect(article).not.toHaveProperty('content');
    expect(article).not.toHaveProperty('search_vector');
    expect(article.bookmark_types).toEqual(['favorite']);
    expect(toOfflineArticle({ title: 'no id' })).toBeNull();
  });

  it('replaces the whole cache on a changed snapshot (no merge)', () => {
    const first = applySnapshot(emptyRecord('user-a'), snapshot(), new Set(), 1000);
    expect(first.items.map((a) => a.id)).toEqual(['a1', 'a2']);
    const second = applySnapshot(
      first,
      snapshot({ cursor: 'c2', items: [raw('a3')] }),
      new Set(),
      2000,
    );
    expect(second.items.map((a) => a.id)).toEqual(['a3']);
    expect(second.cursor).toBe('c2');
    expect(second.updatedAt).toBe(2000);
  });

  it('only advances the cursor when nothing relevant changed', () => {
    const first = applySnapshot(emptyRecord('user-a'), snapshot(), new Set(), 1000);
    const next = applySnapshot(
      first,
      snapshot({ changed: false, items: null, cursor: 'c2' }),
      new Set(),
      5000,
    );
    expect(next.items).toBe(first.items);
    expect(next.cursor).toBe('c2');
    expect(next.updatedAt).toBe(5000);
  });

  it('rejects a snapshot for another account', () => {
    expect(() =>
      applySnapshot(emptyRecord('user-a'), snapshot({ account_id: 'user-b' }), new Set()),
    ).toThrow();
  });

  it('drops revoked pending operations but keeps ones queued after the request', () => {
    let record = applySnapshot(emptyRecord('user-a'), snapshot(), new Set(), 1000);
    record = queueOperation(record, 'a1', 'read', true);
    record = queueOperation(record, 'a2', 'favorite', true);
    // Request asked only about a1; a2 was queued meanwhile and must survive.
    const next = applySnapshot(record, snapshot({ authorized_article_ids: [] }), new Set(['a1']));
    expect(next.pending).toEqual([{ articleId: 'a2', kind: 'favorite', enabled: true }]);
  });

  it('coalesces the queue per (article, kind) and enforces the 100-article limit', () => {
    const items = Array.from({ length: PENDING_ARTICLE_LIMIT + 1 }, (_, i) => raw(`a${i}`));
    let record = applySnapshot(emptyRecord('user-a'), snapshot({ items }), new Set());
    record = queueOperation(record, 'a0', 'read', true);
    record = queueOperation(record, 'a0', 'read', false);
    expect(record.pending).toEqual([{ articleId: 'a0', kind: 'read', enabled: false }]);
    for (let i = 1; i < PENDING_ARTICLE_LIMIT; i++)
      record = queueOperation(record, `a${i}`, 'read', true);
    expect(() => queueOperation(record, `a${PENDING_ARTICLE_LIMIT}`, 'read', true)).toThrow(/100/);
    // Another kind for an article already queued is still allowed.
    expect(() => queueOperation(record, 'a0', 'favorite', true)).not.toThrow();
    expect(() => queueOperation(record, 'missing', 'read', true)).toThrow();
  });

  it('acknowledges only the exact replayed operation', () => {
    let record = applySnapshot(emptyRecord('user-a'), snapshot(), new Set());
    record = queueOperation(record, 'a1', 'read', false);
    const acked = acknowledge(record, { articleId: 'a1', kind: 'read', enabled: true });
    expect(acked.pending).toHaveLength(1);
    expect(acknowledge(record, { articleId: 'a1', kind: 'read', enabled: false }).pending).toEqual(
      [],
    );
  });

  it('overlays queued state on the snapshot for the optimistic view', () => {
    let record = applySnapshot(
      emptyRecord('user-a'),
      snapshot({ items: [raw('a1', { is_read: true, bookmark_types: ['read_later'] })] }),
      new Set(),
    );
    record = queueOperation(record, 'a1', 'read', false);
    record = queueOperation(record, 'a1', 'favorite', true);
    const [view] = overlay(record);
    expect(view).toMatchObject({
      read: false,
      favorite: true,
      readLater: true,
      pending: ['read', 'favorite'],
    });
  });

  it('treats caches older than 24 hours (or never synced) as unusable', () => {
    const record = { ...emptyRecord('user-a'), updatedAt: 10_000 };
    expect(isFresh(record, 10_000 + CACHE_TTL_MS - 1)).toBe(true);
    expect(isFresh(record, 10_000 + CACHE_TTL_MS)).toBe(false);
    expect(isFresh(emptyRecord('user-a'))).toBe(false);
  });
});
