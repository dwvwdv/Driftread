import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { Subject, of, throwError } from 'rxjs';
import { ReadingStreamService } from './reading-stream';
import { AuthService } from './auth';
import { MeService } from './me';
import { MarkAllReadResult, PaginatedStream, StreamArticle, UnreadSummary } from '../models';

const article = (id: string, overrides: Partial<StreamArticle> = {}): StreamArticle => ({
  id,
  feed_id: 'feed-1',
  feed_title: 'Feed One',
  title: `Article ${id}`,
  url: `https://example.com/${id}`,
  summary: null,
  author: null,
  published_at: '2026-08-14T10:00:00+00:00',
  fetched_at: '2026-08-14T10:00:00+00:00',
  is_read: false,
  read_at: null,
  ...overrides,
});

describe('ReadingStreamService', () => {
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  let me: {
    getUnreadCounts: () => ReturnType<MeService['getUnreadCounts']>;
    getStream: (opts: unknown) => ReturnType<MeService['getStream']>;
    markRead: (id: string) => ReturnType<MeService['markRead']>;
    markUnread: (id: string) => ReturnType<MeService['markUnread']>;
    markAllRead: (body: unknown) => ReturnType<MeService['markAllRead']>;
  };
  let unreadSummary: UnreadSummary;
  let streamPage: PaginatedStream;
  let streamCalls: unknown[];

  function setup() {
    session = signal<{ user: { id: string } } | null>({ user: { id: 'user-1' } });
    unreadSummary = {
      total_unread: 2,
      feeds: [{ feed_id: 'feed-1', feed_title: 'Feed One', unread_count: 2 }],
    };
    streamPage = {
      items: [article('a', { is_read: false }), article('b', { is_read: false })],
      next_cursor: null,
    };
    streamCalls = [];

    me = {
      getUnreadCounts: () => of(unreadSummary),
      getStream: (opts: unknown) => {
        streamCalls.push(opts);
        return of(streamPage);
      },
      markRead: () => of(undefined),
      markUnread: () => of(undefined),
      markAllRead: () => of({ marked: 0 } as MarkAllReadResult),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        { provide: AuthService, useValue: { session } },
        { provide: MeService, useValue: me },
      ],
    });

    return TestBed.inject(ReadingStreamService);
  }

  it('loads unread counts once a user id is available', () => {
    const svc = setup();
    TestBed.flushEffects();

    expect(svc.totalUnread()).toBe(2);
    expect(svc.feedCounts()).toEqual(unreadSummary.feeds);
    expect(svc.countsLoaded()).toBe(true);
  });

  it('resets state when the signed-in identity changes', () => {
    const svc = setup();
    TestBed.flushEffects();
    expect(svc.totalUnread()).toBe(2);

    session.set(null);
    TestBed.flushEffects();

    expect(svc.totalUnread()).toBe(0);
    expect(svc.countsLoaded()).toBe(false);
    expect(svc.items()).toEqual([]);
  });

  it('load() replaces items and captures the next cursor', () => {
    const svc = setup();
    streamPage = { items: [article('a')], next_cursor: 'cursor-1' };

    svc.load({ feedId: null, unreadOnly: false });

    expect(svc.items().map((a) => a.id)).toEqual(['a']);
    expect(svc.hasMore()).toBe(true);
  });

  it('load() discards a response from a superseded (older) filter call', () => {
    const svc = setup();
    const subjects: Subject<PaginatedStream>[] = [];
    me.getStream = (opts: unknown) => {
      streamCalls.push(opts);
      const s = new Subject<PaginatedStream>();
      subjects.push(s);
      return s;
    };

    svc.load({ feedId: 'feed-1' });
    svc.load({ feedId: 'feed-2' });

    // The newer request (feed-2) resolves first, then the older (feed-1)
    // request resolves after it — its response must be discarded, not
    // clobber the newer filter's items/cursor.
    subjects[1].next({ items: [article('b')], next_cursor: 'cursor-b' });
    subjects[1].complete();
    subjects[0].next({ items: [article('a')], next_cursor: 'cursor-a' });
    subjects[0].complete();

    expect(svc.items().map((a) => a.id)).toEqual(['b']);
    expect(svc.hasMore()).toBe(true);
  });

  it('loadMore() appends to the existing items and updates the cursor', () => {
    const svc = setup();
    streamPage = { items: [article('a')], next_cursor: 'cursor-1' };
    svc.load({});

    streamPage = { items: [article('b')], next_cursor: null };
    svc.loadMore({});

    expect(svc.items().map((a) => a.id)).toEqual(['a', 'b']);
    expect(svc.hasMore()).toBe(false);
  });

  it('loadMore() is a no-op without a next cursor', () => {
    const svc = setup();
    streamPage = { items: [article('a')], next_cursor: null };
    svc.load({});
    const callsBefore = streamCalls.length;

    svc.loadMore({});

    expect(streamCalls.length).toBe(callsBefore);
  });

  it('load() clears a loadingMore flag left stuck by a superseded loadMore', () => {
    const svc = setup();
    streamPage = { items: [article('a')], next_cursor: 'cursor-1' };
    svc.load({ feedId: 'feed-1' });

    const pendingLoadMore = new Subject<PaginatedStream>();
    me.getStream = (opts: unknown) => {
      streamCalls.push(opts);
      return pendingLoadMore;
    };
    svc.loadMore({ feedId: 'feed-1' });
    expect(svc.loadingMore()).toBe(true);

    // Filter changes while that load-more is still in flight — a fresh
    // load() should clear the stuck flag immediately, not wait for the
    // superseded response (which never gets applied at all).
    streamPage = { items: [article('b')], next_cursor: null };
    me.getStream = (opts: unknown) => {
      streamCalls.push(opts);
      return of(streamPage);
    };
    svc.load({ feedId: 'feed-2' });

    expect(svc.loadingMore()).toBe(false);

    pendingLoadMore.next({ items: [article('stale')], next_cursor: 'stale-cursor' });
    pendingLoadMore.complete();

    expect(svc.loadingMore()).toBe(false);
    expect(svc.items().map((a) => a.id)).toEqual(['b']);
  });

  it('markRead optimistically flips the row and decrements unread counts', () => {
    const svc = setup();
    TestBed.flushEffects();
    svc.load({});

    svc.markRead('a');

    expect(svc.items().find((a) => a.id === 'a')?.is_read).toBe(true);
    expect(svc.totalUnread()).toBe(1);
    expect(svc.feedCounts()[0].unread_count).toBe(1);
  });

  it('markRead rolls back on failure', () => {
    const svc = setup();
    TestBed.flushEffects();
    svc.load({});
    me.markRead = () => throwError(() => new Error('boom'));

    let errored = false;
    svc.markRead('a', () => (errored = true));

    expect(errored).toBe(true);
    expect(svc.items().find((a) => a.id === 'a')?.is_read).toBe(false);
    expect(svc.totalUnread()).toBe(2);
  });

  it('loadCounts() discards a snapshot that predates a local optimistic mutation', () => {
    const svc = setup();
    TestBed.flushEffects(); // initial automatic load resolves synchronously: totalUnread = 2
    svc.load({});
    expect(svc.totalUnread()).toBe(2);

    // A reload starts (e.g. re-entering the page) but is left in flight...
    const counts = new Subject<UnreadSummary>();
    me.getUnreadCounts = () => counts;
    svc.loadCounts();

    // ...while a markRead commits synchronously and moves totalUnread to 1.
    svc.markRead('a');
    expect(svc.totalUnread()).toBe(1);

    // The in-flight snapshot — requested before that mutation — resolves
    // after it with the pre-mutation count; applying it would silently
    // undo the optimistic decrement.
    counts.next({
      total_unread: 2,
      feeds: [{ feed_id: 'feed-1', feed_title: 'Feed One', unread_count: 2 }],
    });
    counts.complete();

    expect(svc.totalUnread()).toBe(1);
    expect(svc.countsLoaded()).toBe(true);
  });

  it('loadCounts() ignores a failure from a superseded (older) request', () => {
    const svc = setup();
    const first = new Subject<UnreadSummary>();
    const second = new Subject<UnreadSummary>();
    let call = 0;
    me.getUnreadCounts = () => (++call === 1 ? first : second);

    svc.loadCounts();
    let errored = false;
    // A second loadCounts() call (e.g. a rapid reload) supersedes the first.
    svc.loadCounts(() => (errored = true));

    second.next({ total_unread: 5, feeds: [] });
    second.complete();
    expect(svc.totalUnread()).toBe(5);
    expect(svc.countsLoading()).toBe(false);

    // The superseded first request fails after the second already
    // succeeded — its error must not surface or touch loading state.
    first.error(new Error('boom'));

    expect(errored).toBe(false);
    expect(svc.totalUnread()).toBe(5);
    expect(svc.countsLoading()).toBe(false);
  });

  it('markRead ignores a second call while one is already pending', () => {
    const svc = setup();
    TestBed.flushEffects();
    svc.load({});
    const pending = new Subject<void>();
    let calls = 0;
    me.markRead = () => {
      calls++;
      return pending;
    };

    svc.markRead('a');
    svc.markRead('a');

    expect(calls).toBe(1);
  });

  it('markUnread optimistically flips the row back and restores unread counts', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: true, read_at: '2026-08-14T10:00:00+00:00' })], next_cursor: null };
    svc.load({});

    svc.markUnread('a');

    expect(svc.items()[0].is_read).toBe(false);
    expect(svc.totalUnread()).toBe(3);
  });

  it('markAllReadInView marks only currently-unread rows and sends their ids', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = {
      items: [article('a', { is_read: false }), article('b', { is_read: true })],
      next_cursor: null,
    };
    svc.load({});

    let sentBody: unknown;
    me.markAllRead = (body: unknown) => {
      sentBody = body;
      return of({ marked: 1 });
    };

    let marked = -1;
    svc.markAllReadInView((n) => (marked = n));

    expect(sentBody).toEqual({ article_ids: ['a'] });
    expect(marked).toBe(1);
    expect(svc.items().every((a) => a.is_read)).toBe(true);
    expect(svc.totalUnread()).toBe(1); // one unread row (a) cleared out of 2
  });

  it('markAllReadInView is a no-op when nothing is unread', () => {
    const svc = setup();
    streamPage = { items: [article('a', { is_read: true })], next_cursor: null };
    svc.load({});
    let calls = 0;
    me.markAllRead = () => {
      calls++;
      return of({ marked: 0 });
    };

    let marked = -1;
    svc.markAllReadInView((n) => (marked = n));

    expect(calls).toBe(0);
    expect(marked).toBe(0);
  });

  it('markAllReadInView rolls back the optimistic update on failure', () => {
    const svc = setup();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});
    me.markAllRead = () => throwError(() => new Error('boom'));

    let errored = false;
    svc.markAllReadInView(undefined, () => (errored = true));

    expect(errored).toBe(true);
    expect(svc.items()[0].is_read).toBe(false);
  });

  it('markAllReadInView batches ids in view past the backend cap into multiple requests', () => {
    const svc = setup();
    const items = Array.from({ length: 620 }, (_, i) =>
      article(`a${i}`, { is_read: false, feed_id: 'feed-1' }),
    );
    streamPage = { items, next_cursor: null };
    svc.load({});

    const sentBodies: { article_ids: string[] }[] = [];
    me.markAllRead = (body: unknown) => {
      const b = body as { article_ids: string[] };
      sentBodies.push(b);
      return of({ marked: b.article_ids.length });
    };

    let marked = -1;
    svc.markAllReadInView((n) => (marked = n));

    expect(sentBodies.length).toBe(2);
    expect(sentBodies[0].article_ids.length).toBe(500);
    expect(sentBodies[1].article_ids.length).toBe(120);
    expect(marked).toBe(620);
    expect(svc.items().every((a) => a.is_read)).toBe(true);
  });

  it('markAllReadInView reconciles a partially failed batch instead of reverting the whole set', () => {
    const svc = setup();
    const items = Array.from({ length: 620 }, (_, i) =>
      article(`a${i}`, { is_read: false, feed_id: 'feed-1' }),
    );
    streamPage = { items, next_cursor: null };
    svc.load({});

    // The first (500-id) batch commits server-side; the second (120-id)
    // batch fails — the two aren't atomic as a set.
    let call = 0;
    me.markAllRead = (body: unknown) => {
      const b = body as { article_ids: string[] };
      call++;
      return call === 1 ? of({ marked: b.article_ids.length }) : throwError(() => new Error('boom'));
    };

    let marked = -1;
    let errored = false;
    svc.markAllReadInView(
      (n) => (marked = n),
      () => (errored = true),
    );

    expect(errored).toBe(true);
    expect(marked).toBe(500);
    // First 500 (the succeeded batch) stay read; the last 120 (the failed
    // batch) are reverted back to unread rather than left showing as read
    // client-side while the server never actually recorded them.
    expect(items.slice(0, 500).every((a) => svc.items().find((x) => x.id === a.id)?.is_read)).toBe(
      true,
    );
    expect(
      items.slice(500).every((a) => !svc.items().find((x) => x.id === a.id)?.is_read),
    ).toBe(true);
  });

  it('markAllReadInView ignores its outcome after the signed-in identity changes', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});

    const pending = new Subject<MarkAllReadResult>();
    me.markAllRead = () => pending;

    let called = false;
    svc.markAllReadInView(
      () => (called = true),
      () => (called = true),
    );

    // The reader signs out while the request is still in flight — the
    // identity effect resets items/counts for the new (signed-out) state.
    session.set(null);
    TestBed.flushEffects();
    expect(svc.items()).toEqual([]);

    // A late failure for the old user's request must not resurrect the old
    // user's items or touch the counts of the new (signed-out) session.
    pending.error(new Error('boom'));

    expect(called).toBe(false);
    expect(svc.items()).toEqual([]);
    expect(svc.totalUnread()).toBe(0);
  });

  it('markAllReadInScope sends the given feed id and refreshes counts on success', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});

    let sentBody: unknown;
    me.markAllRead = (body: unknown) => {
      sentBody = body;
      return of({ marked: 5 });
    };
    unreadSummary = { total_unread: 0, feeds: [] };

    let marked = -1;
    svc.markAllReadInScope('feed-1', (n) => (marked = n));

    expect(sentBody).toEqual({ feed_id: 'feed-1' });
    expect(marked).toBe(5);
    // Locally-visible row in that feed flips to read immediately...
    expect(svc.items()[0].is_read).toBe(true);
    // ...and the authoritative counts come from the follow-up refresh.
    expect(svc.totalUnread()).toBe(0);
  });

  it('markAllReadInScope surfaces a failure of its post-write counts refresh', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});

    me.markAllRead = () => of({ marked: 5 });
    me.getUnreadCounts = () => throwError(() => new Error('boom'));

    let marked = -1;
    let errored = false;
    svc.markAllReadInScope(
      'feed-1',
      (n) => (marked = n),
      () => (errored = true),
    );

    // The write itself succeeded — the row still flips and onSuccess still
    // fires — but the refresh failure is also surfaced rather than leaving
    // the stale counts silently wrong.
    expect(marked).toBe(5);
    expect(svc.items()[0].is_read).toBe(true);
    expect(errored).toBe(true);
  });

  it('markAllReadInScope with no feed id sends an unscoped ("everything") request', () => {
    const svc = setup();
    let sentBody: unknown;
    me.markAllRead = (body: unknown) => {
      sentBody = body;
      return of({ marked: 9 });
    };

    svc.markAllReadInScope(null);

    expect(sentBody).toEqual({});
  });

  it('markAllReadInScope ignores its outcome after the signed-in identity changes', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});

    const pending = new Subject<MarkAllReadResult>();
    me.markAllRead = () => pending;

    let called = false;
    svc.markAllReadInScope(
      'feed-1',
      () => (called = true),
      () => (called = true),
    );

    session.set(null);
    TestBed.flushEffects();

    pending.next({ marked: 3 });
    pending.complete();

    expect(called).toBe(false);
    expect(svc.items()).toEqual([]);
  });

  it('markRead/markUnread rollback callbacks ignore a late failure after the identity changes', () => {
    const svc = setup();
    TestBed.flushEffects();
    streamPage = { items: [article('a', { is_read: false })], next_cursor: null };
    svc.load({});

    const pending = new Subject<void>();
    me.markRead = () => pending;

    let errored = false;
    svc.markRead('a', () => (errored = true));

    session.set(null);
    TestBed.flushEffects();
    expect(svc.items()).toEqual([]);

    pending.error(new Error('boom'));

    expect(errored).toBe(false);
    expect(svc.items()).toEqual([]);
    expect(svc.totalUnread()).toBe(0);
  });

  it('loadCounts() supersedes an older still-in-flight loadCounts() call', () => {
    const svc = setup();
    const first = new Subject<UnreadSummary>();
    const second = new Subject<UnreadSummary>();
    let call = 0;
    me.getUnreadCounts = () => (++call === 1 ? first : second);

    svc.loadCounts(); // e.g. the automatic page-entry fetch
    svc.loadCounts(); // e.g. markAllReadInScope's post-write authoritative refresh

    // The newer request resolves first with the authoritative post-write
    // count, then the older, pre-write request resolves after it — it must
    // not overwrite the authoritative refresh.
    second.next({ total_unread: 1, feeds: [] });
    second.complete();
    first.next({ total_unread: 9, feeds: [] });
    first.complete();

    expect(svc.totalUnread()).toBe(1);
  });
});
