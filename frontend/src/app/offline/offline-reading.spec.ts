import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
  TestRequest,
} from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { IDBFactory } from 'fake-indexeddb';
import { authInterceptor } from '../interceptors/auth.interceptor';
import { AuthService } from '../services/auth';
import { ReadingStreamService } from '../services/reading-stream';
import { NetworkStatusService } from './network-status';
import { AUTO_REFRESH_MS, OfflineReadingService } from './offline-reading';
import { CACHE_TTL_MS, SyncSnapshot } from './offline-state';
import { OfflineStore } from './offline-store';

type FakeSession = { user: { id: string }; access_token: string } | null;

const item = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  feed_id: 'f1',
  feed_title: 'Feed',
  title: `T ${id}`,
  url: `https://example.com/${id}`,
  summary: 'summary',
  author: null,
  published_at: null,
  timeline_at: null,
  is_read: false,
  bookmark_types: [],
  content: 'never stored',
  ...extra,
});

const snapshot = (account: string, overrides: Partial<SyncSnapshot> = {}): SyncSnapshot => ({
  account_id: account,
  cursor: `cursor-${account}`,
  changed: true,
  items: [item('a1'), item('a2')],
  authorized_article_ids: [],
  ...overrides,
});

const isSync = (r: { url: string; method: string }) =>
  r.method === 'GET' && r.url.endsWith('/api/me/sync');
const isReplay = (r: { url: string; method: string }) =>
  r.method === 'POST' && r.url.endsWith('/api/me/sync/operations');

describe('OfflineReadingService', () => {
  let session: ReturnType<typeof signal<FakeSession>>;
  let online: ReturnType<typeof signal<boolean>>;
  let http: HttpTestingController;
  let store: OfflineStore;
  let stream: { loadCounts: ReturnType<typeof vi.fn> };

  const as = (id: string): FakeSession => ({ user: { id }, access_token: `token-${id}` });

  function setup(initial: FakeSession = as('user-a'), isOnline = true) {
    globalThis.indexedDB = new IDBFactory();
    session = signal<FakeSession>(initial);
    online = signal(isOnline);
    stream = { loadCounts: vi.fn() };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        {
          provide: AuthService,
          useValue: {
            session,
            get accessToken() {
              return session()?.access_token ?? null;
            },
          },
        },
        { provide: NetworkStatusService, useValue: { online } },
        { provide: ReadingStreamService, useValue: stream },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    store = TestBed.inject(OfflineStore);
    const svc = TestBed.inject(OfflineReadingService);
    TestBed.flushEffects();
    return svc;
  }

  const next = (match: (r: { url: string; method: string }) => boolean): Promise<TestRequest> =>
    vi.waitFor(() => http.expectOne(match));

  async function initialSync(svc: OfflineReadingService, account = 'user-a') {
    await vi.waitFor(() => expect(svc.loaded()).toBe(true));
    const done = svc.sync();
    (await next(isSync)).flush(snapshot(account));
    expect((await done).ok).toBe(true);
  }

  afterEach(() => http.verify());

  it('caches the snapshot per user with the session token and without bodies', async () => {
    const svc = setup();
    await vi.waitFor(() => expect(svc.loaded()).toBe(true));
    const done = svc.sync();
    const req = await next(isSync);
    expect(req.request.headers.get('Authorization')).toBe('Bearer token-user-a');
    req.flush(snapshot('user-a'));
    await done;

    expect(svc.items().map((v) => v.article.id)).toEqual(['a1', 'a2']);
    const stored = await store.read('user-a');
    expect(stored?.cursor).toBe('cursor-user-a');
    expect(stored?.items[0]).not.toHaveProperty('content');
    expect(await store.owner()).toBe('user-a');
  });

  it('applies invalidation: unchanged keeps items, changed replaces the snapshot', async () => {
    const svc = setup();
    await initialSync(svc);

    let done = svc.sync();
    let req = await next(isSync);
    expect(req.request.params.get('cursor')).toBe('cursor-user-a');
    req.flush(snapshot('user-a', { changed: false, items: null, cursor: 'c2' }));
    await done;
    expect(svc.items().map((v) => v.article.id)).toEqual(['a1', 'a2']);

    done = svc.sync();
    req = await next(isSync);
    req.flush(snapshot('user-a', { items: [item('a9')], cursor: 'c3', reset: true }));
    await done;
    expect(svc.items().map((v) => v.article.id)).toEqual(['a9']);
    expect((await store.read('user-a'))?.cursor).toBe('c3');
  });

  it('queues offline, then replays on reconnect with the original token and re-pulls', async () => {
    const svc = setup();
    await initialSync(svc);
    online.set(false);
    TestBed.flushEffects();

    const result = await svc.setState('a1', 'read', true);
    expect(result).toEqual({ queued: true, replay: null });
    expect(svc.items()[0]).toMatchObject({ read: true, pending: ['read'] });
    expect((await store.read('user-a'))?.pending).toHaveLength(1);
    http.expectNone(isReplay);

    online.set(true);
    TestBed.flushEffects();
    const pull = await next(isSync);
    expect(pull.request.params.getAll('pending_ids')).toEqual(['a1']);
    pull.flush(snapshot('user-a', { authorized_article_ids: ['a1'] }));

    const replay = await next(isReplay);
    expect(replay.request.headers.get('Authorization')).toBe('Bearer token-user-a');
    expect(replay.request.body).toEqual({ article_id: 'a1', kind: 'read', enabled: true });
    replay.flush(null, { status: 204, statusText: 'No Content' });

    (await next(isSync)).flush(
      snapshot('user-a', { items: [item('a1', { is_read: true }), item('a2')] }),
    );
    await vi.waitFor(() => expect(svc.items()[0]).toMatchObject({ read: true, pending: [] }));
    expect(svc.pendingCount()).toBe(0);
    await vi.waitFor(() => expect(stream.loadCounts).toHaveBeenCalled());
  });

  it('drops a 404 replay and falls back to the server snapshot (rollback)', async () => {
    const svc = setup();
    await initialSync(svc);

    const result = await svc.setState('a2', 'favorite', true);
    expect(svc.items()[1].favorite).toBe(true);
    if (!result.queued || !result.replay) throw new Error('expected an online replay');

    (await next(isSync)).flush(snapshot('user-a', { authorized_article_ids: ['a2'] }));
    (await next(isReplay)).flush('gone', { status: 404, statusText: 'Not Found' });
    (await next(isSync)).flush(snapshot('user-a'));

    expect(await result.replay).toEqual({ ok: true, dropped: 1 });
    expect(svc.items()[1]).toMatchObject({ favorite: false, pending: [] });
  });

  it('keeps the queue when the server is busy (503) or the network fails', async () => {
    const svc = setup();
    await initialSync(svc);
    const result = await svc.setState('a1', 'read_later', true);
    if (!result.queued || !result.replay) throw new Error('expected an online replay');

    (await next(isSync)).flush(snapshot('user-a', { authorized_article_ids: ['a1'] }));
    (await next(isReplay)).flush('busy', { status: 503, statusText: 'Busy' });

    expect(await result.replay).toEqual({ ok: false, dropped: 0 });
    expect(svc.pendingCount()).toBe(1);
    expect(svc.items()[0].readLater).toBe(true);
    expect(svc.error()).toContain('保留');

    const retry = svc.sync();
    (await next(isSync)).error(new ProgressEvent('error'));
    expect((await retry).ok).toBe(false);
    expect((await store.read('user-a'))?.pending).toHaveLength(1);
  });

  it('rolls back the optimistic state when the queue cannot be persisted', async () => {
    const svc = setup();
    await initialSync(svc);
    online.set(false);
    TestBed.flushEffects();
    vi.spyOn(store, 'update').mockRejectedValueOnce(new Error('QuotaExceededError'));

    const result = await svc.setState('a1', 'read', true);
    expect(result.queued).toBe(false);
    expect(svc.items()[0]).toMatchObject({ read: false, pending: [] });
    expect(svc.error()).toContain('QuotaExceededError');
  });

  it('switching account deletes A cache and queue; B never sends A operations', async () => {
    const svc = setup();
    await initialSync(svc);
    online.set(false);
    TestBed.flushEffects();
    await svc.setState('a1', 'read', true);

    session.set(as('user-b'));
    TestBed.flushEffects();
    await vi.waitFor(async () => expect(await store.owner()).toBe('user-b'));
    expect(await store.read('user-a')).toBeNull();
    expect(svc.items()).toEqual([]);
    expect(svc.pendingCount()).toBe(0);

    online.set(true);
    TestBed.flushEffects();
    const done = svc.sync();
    const pull = await next(isSync);
    expect(pull.request.headers.get('Authorization')).toBe('Bearer token-user-b');
    expect(pull.request.params.has('pending_ids')).toBe(false);
    pull.flush(snapshot('user-b'));
    await done;
    http.expectNone(isReplay);
  });

  it('an account switch mid-sync stops before replaying and does not write A data', async () => {
    const svc = setup();
    await initialSync(svc);
    online.set(false);
    TestBed.flushEffects();
    await svc.setState('a1', 'read', true);

    online.set(true);
    TestBed.flushEffects();
    const pull = await next(isSync);
    session.set(as('user-b'));
    TestBed.flushEffects();
    pull.flush(snapshot('user-a', { authorized_article_ids: ['a1'] }));

    await vi.waitFor(async () => expect(await store.owner()).toBe('user-b'));
    await new Promise((r) => setTimeout(r, 20));
    http.expectNone(isReplay);
    expect(await store.read('user-a')).toBeNull();
  });

  it('background-syncs a stale cache when the page becomes visible again (still online)', async () => {
    const svc = setup();
    await initialSync(svc);
    // Fresh cache: becoming visible does not sync.
    document.dispatchEvent(new Event('visibilitychange'));
    http.expectNone(isSync);
    // The device slept past the refresh threshold without going offline.
    const realNow = Date.now();
    const spy = vi.spyOn(Date, 'now').mockReturnValue(realNow + AUTO_REFRESH_MS + 1000);
    document.dispatchEvent(new Event('visibilitychange'));
    const req = await next(isSync);
    expect(req.request.headers.get('Authorization')).toBe('Bearer token-user-a');
    spy.mockRestore();
    req.flush(snapshot('user-a', { changed: false, items: null, cursor: 'c2' }));
    await vi.waitFor(async () => expect((await store.read('user-a'))?.cursor).toBe('c2'));
  });

  it('background-syncs a page left open once the cache passes the refresh threshold', async () => {
    const svc = setup();
    // Make the first snapshot look like it was taken just under 15 minutes ago.
    const realNow = Date.now();
    const spy = vi.spyOn(Date, 'now').mockReturnValue(realNow - AUTO_REFRESH_MS + 300);
    await initialSync(svc);
    spy.mockRestore();
    TestBed.flushEffects(); // the app's change detection runs the timer effect
    http.expectNone(isSync);
    // No visibility, network or record change: only the refresh timer fires.
    const req = await vi.waitFor(() => http.expectOne(isSync), { timeout: 2000 });
    req.flush(snapshot('user-a', { changed: false, items: null, cursor: 'c3' }));
    await vi.waitFor(async () => expect((await store.read('user-a'))?.cursor).toBe('c3'));
  });

  it('signing out in this runtime clears every cached account', async () => {
    const svc = setup();
    await initialSync(svc);
    session.set(null);
    TestBed.flushEffects();
    await vi.waitFor(async () => expect(await store.owner()).toBeNull());
    expect(await store.read('user-a')).toBeNull();
    expect(svc.mode()).toBe('none');
  });

  it('clearAll() wipes the store immediately (explicit sign-out)', async () => {
    const svc = setup();
    await initialSync(svc);
    await svc.clearAll();
    expect(await store.read('user-a')).toBeNull();
    expect(svc.items()).toEqual([]);
  });
});

describe('OfflineReadingService dormant mode', () => {
  it('shows the last owner cache only while offline, never queues, hides when online', async () => {
    globalThis.indexedDB = new IDBFactory();
    const seed = new OfflineStore();
    await seed.claim('user-a');
    await seed.update('user-a', () => ({
      userId: 'user-a',
      items: [
        {
          id: 'a1',
          feed_id: 'f',
          feed_title: null,
          title: 't',
          url: '',
          summary: null,
          author: null,
          published_at: null,
          timeline_at: null,
          is_read: false,
          bookmark_types: [],
        },
      ],
      pending: [],
      cursor: 'c',
      updatedAt: Date.now(),
    }));

    const online = signal(false);
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { session: signal(null), accessToken: null } },
        { provide: NetworkStatusService, useValue: { online } },
        { provide: ReadingStreamService, useValue: { loadCounts: vi.fn() } },
      ],
    });
    const svc = TestBed.inject(OfflineReadingService);
    TestBed.flushEffects();

    await vi.waitFor(() => expect(svc.mode()).toBe('dormant'));
    expect(svc.items().map((v) => v.article.id)).toEqual(['a1']);
    expect((await svc.setState('a1', 'read', true)).queued).toBe(false);

    online.set(true);
    TestBed.flushEffects();
    expect(svc.record()).toBeNull();
    // Not deleted: the same user signing back in keeps it.
    expect(await seed.read('user-a')).not.toBeNull();
  });

  it('does not show a cache older than 24 hours', async () => {
    globalThis.indexedDB = new IDBFactory();
    const seed = new OfflineStore();
    await seed.claim('user-a');
    await seed.update('user-a', () => ({
      userId: 'user-a',
      items: [],
      pending: [],
      cursor: 'c',
      updatedAt: Date.now() - CACHE_TTL_MS - 1,
    }));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { session: signal(null), accessToken: null } },
        { provide: NetworkStatusService, useValue: { online: signal(false) } },
        { provide: ReadingStreamService, useValue: { loadCounts: vi.fn() } },
      ],
    });
    const svc = TestBed.inject(OfflineReadingService);
    TestBed.flushEffects();
    await vi.waitFor(() => expect(svc.loaded()).toBe(true));
    expect(svc.record()).toBeNull();
    expect(svc.expired()).toBe(true);
  });

  it('hides a shown cache once it passes the TTL while the page stays open', async () => {
    globalThis.indexedDB = new IDBFactory();
    const seed = new OfflineStore();
    await seed.claim('user-a');
    // Expires in 400ms: the page is open (and offline) across the boundary.
    await seed.update('user-a', () => ({
      userId: 'user-a',
      items: [],
      pending: [],
      cursor: 'c',
      updatedAt: Date.now() - CACHE_TTL_MS + 400,
    }));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { session: signal(null), accessToken: null } },
        { provide: NetworkStatusService, useValue: { online: signal(false) } },
        { provide: ReadingStreamService, useValue: { loadCounts: vi.fn() } },
      ],
    });
    const svc = TestBed.inject(OfflineReadingService);
    TestBed.flushEffects();
    await vi.waitFor(() => expect(svc.record()).not.toBeNull());
    expect(svc.expired()).toBe(false);
    TestBed.flushEffects(); // the app's change detection runs the expiry effect
    // Nothing else changes (no record, mode or network update): only the
    // expiry timer can make the computed values re-evaluate.
    await vi.waitFor(() => expect(svc.record()).toBeNull(), { timeout: 2000 });
    expect(svc.expired()).toBe(true);
  });

  it('re-checks the TTL when the page becomes visible again (resumed PWA)', async () => {
    globalThis.indexedDB = new IDBFactory();
    const seed = new OfflineStore();
    await seed.claim('user-a');
    await seed.update('user-a', () => ({
      userId: 'user-a',
      items: [],
      pending: [],
      cursor: 'c',
      updatedAt: Date.now() - CACHE_TTL_MS + 60_000,
    }));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { session: signal(null), accessToken: null } },
        { provide: NetworkStatusService, useValue: { online: signal(false) } },
        { provide: ReadingStreamService, useValue: { loadCounts: vi.fn() } },
      ],
    });
    const svc = TestBed.inject(OfflineReadingService);
    TestBed.flushEffects();
    await vi.waitFor(() => expect(svc.record()).not.toBeNull());
    // Simulate the device sleeping past the TTL: the clock jumps, timers have
    // not fired yet, then the page becomes visible.
    const realNow = Date.now();
    const spy = vi.spyOn(Date, 'now').mockReturnValue(realNow + 2 * 60_000);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(svc.record()).toBeNull();
    expect(svc.expired()).toBe(true);
    spy.mockRestore();
  });
});
