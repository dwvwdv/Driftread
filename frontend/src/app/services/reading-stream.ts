import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { catchError, forkJoin, map, of } from 'rxjs';
import { AuthService } from './auth';
import { MeService } from './me';
import { FeedUnreadCount, StreamArticle } from '../models';

/** Matches backend/models.py MarkAllReadRequest.article_ids' `max_length`
 * validation cap — "本頁全部已讀" must batch requests below this or a
 * reader who has loaded more than this many unread articles gets a 422. */
const MARK_ALL_BATCH_SIZE = 500;

function chunk<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

/** Filters the stream page currently applies — shared shape between
 * `load()`/`loadMore()` and the mark-all "explicit scope" call, so the scope
 * a reader sees on screen and the scope a mark-all request actually covers
 * can never drift apart. */
export interface StreamFilters {
  feedId?: string | null;
  unreadOnly?: boolean;
}

/**
 * Backs the reading stream page and, for unread counts, the nav bar badge —
 * the same "single cache shared by more than one component" role
 * SubscriptionService plays for subscribe state (see that file's header
 * comment). Unlike SubscriptionService this does not attempt full
 * request-vs-write race ordering (no `beginFetch`/`asOf` ticketing): the
 * stream is read-mostly, paginated, and every mutation here is scoped to a
 * single signed-in user's own session, so the identity guard below is
 * enough to stop a stale response from a previous account leaking into a new
 * one — see SubscriptionService for the fuller treatment this would need if
 * writes here started coming from more than one place at once.
 */
@Injectable({ providedIn: 'root' })
export class ReadingStreamService {
  private me = inject(MeService);
  private auth = inject(AuthService);

  private _totalUnread = signal(0);
  private _feedCounts = signal<readonly FeedUnreadCount[]>([]);
  private _countsLoaded = signal(false);
  private _countsLoading = signal(false);

  private _items = signal<StreamArticle[]>([]);
  private _nextCursor = signal<string | null>(null);
  private _loading = signal(false);
  private _loadingMore = signal(false);

  /** Article ids with an in-flight read/unread toggle — lets the row show a
   * disabled state instead of racing a double-click against itself. */
  private _pending = signal<ReadonlySet<string>>(new Set());

  totalUnread = this._totalUnread.asReadonly();
  feedCounts = this._feedCounts.asReadonly();
  countsLoaded = this._countsLoaded.asReadonly();
  countsLoading = this._countsLoading.asReadonly();

  items = this._items.asReadonly();
  loading = this._loading.asReadonly();
  loadingMore = this._loadingMore.asReadonly();
  hasMore = computed(() => this._nextCursor() !== null);

  /** Unread count among the currently loaded page(s) — used for the "本頁
   *全部已讀" action's label/disabled state, distinct from `totalUnread`
   * (every subscribed article, including ones not yet loaded). */
  unreadInView = computed(() => this._items().filter((a) => !a.is_read).length);

  /** User id the counts/items above are currently loaded for. Signing out,
   * or switching accounts, resets everything below to an empty, not-yet-
   * loaded state rather than showing the previous account's numbers. */
  private loadedFor: string | null = null;

  /** Bumped by every `load()` call (a fresh page for possibly-new filters).
   * `load()`/`loadMore()` capture it when they fire and only apply their
   * response if it's still current — otherwise a `load()` for an older
   * filter combination that resolves after a newer one would clobber the
   * newer filter's items and cursor (same user, so the `loadedFor` guard
   * alone doesn't catch this). `loadMore()` doesn't bump it: it's
   * continuing the in-view page, not superseding it, but still gets
   * invalidated if a `load()` supersedes it first. */
  private _itemsGeneration = 0;

  /** Bumped by every local optimistic count mutation (`adjustUnreadCount`)
   * *and* by every `loadCounts()` call itself. `loadCounts()` captures it
   * when it fires and discards its response if the generation has moved on
   * by the time it resolves — either because a mutation landed in the
   * meantime (a snapshot requested before a markRead/markUnread/mark-all
   * write would otherwise clobber the more recent optimistic counters), or
   * because a *newer* `loadCounts()` call (e.g. markAllReadInScope's
   * post-write refresh) was issued and its response is authoritative
   * instead — without this, an older in-flight `loadCounts()` resolving
   * after the newer one would overwrite it right back with pre-write
   * numbers. */
  private _countsGeneration = 0;

  constructor() {
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this._itemsGeneration++;
      this._totalUnread.set(0);
      this._feedCounts.set([]);
      this._countsLoaded.set(false);
      this._items.set([]);
      this._nextCursor.set(null);
      this._pending.set(new Set());
      if (userId) this.loadCounts();
    });
  }

  isPending(articleId: string): boolean {
    return this._pending().has(articleId);
  }

  private setPending(articleId: string, pending: boolean): void {
    const next = new Set(this._pending());
    if (pending) next.add(articleId);
    else next.delete(articleId);
    this._pending.set(next);
  }

  loadCounts(onError?: (err: unknown) => void): void {
    const requestedFor = this.loadedFor;
    const generation = ++this._countsGeneration;
    this._countsLoading.set(true);
    this.me.getUnreadCounts().subscribe({
      next: (summary) => {
        if (this.loadedFor !== requestedFor) return;
        this._countsLoading.set(false);
        this._countsLoaded.set(true);
        // A local mutation landed after this fetch started — its snapshot
        // predates that write, so applying it would clobber the more
        // recent optimistic counters. Drop it; the mutation's own
        // success/error handling already keeps the counters correct.
        if (generation !== this._countsGeneration) return;
        this._totalUnread.set(summary.total_unread);
        this._feedCounts.set(summary.feeds);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor || generation !== this._countsGeneration) return;
        this._countsLoading.set(false);
        onError?.(err);
      },
    });
  }

  /** Replaces `items` with the first page for the given filters. */
  load(filters: StreamFilters, onError?: (err: unknown) => void): void {
    const requestedFor = this.loadedFor;
    const generation = ++this._itemsGeneration;
    this._loading.set(true);
    // A fresh load supersedes any load-more in flight for the previous
    // generation — that request's own callback will now bail out on the
    // generation check below without ever clearing this flag itself.
    this._loadingMore.set(false);
    this.me.getStream({ feedId: filters.feedId, unreadOnly: filters.unreadOnly }).subscribe({
      next: (page) => {
        if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
        this._loading.set(false);
        this._items.set(page.items);
        this._nextCursor.set(page.next_cursor);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
        this._loading.set(false);
        onError?.(err);
      },
    });
  }

  /** Appends the next page after the current cursor. No-op if there is no
   * next page or a load-more is already in flight (guards a double
   * "load more" click / scroll-triggered double fire from issuing two
   * overlapping requests for the same page). */
  loadMore(filters: StreamFilters, onError?: (err: unknown) => void): void {
    const cursor = this._nextCursor();
    if (!cursor || this._loadingMore()) return;
    const requestedFor = this.loadedFor;
    const generation = this._itemsGeneration;
    this._loadingMore.set(true);
    this.me
      .getStream({ cursor, feedId: filters.feedId, unreadOnly: filters.unreadOnly })
      .subscribe({
        next: (page) => {
          if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
          this._loadingMore.set(false);
          this._items.update((current) => [...current, ...page.items]);
          this._nextCursor.set(page.next_cursor);
        },
        error: (err: unknown) => {
          if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
          this._loadingMore.set(false);
          onError?.(err);
        },
      });
  }

  private patchItem(articleId: string, patch: Partial<StreamArticle>): StreamArticle | null {
    let patched: StreamArticle | null = null;
    this._items.update((items) =>
      items.map((a) => {
        if (a.id !== articleId) return a;
        patched = { ...a, ...patch };
        return patched;
      }),
    );
    return patched;
  }

  private adjustUnreadCount(feedId: string, delta: number): void {
    this._countsGeneration++;
    this._totalUnread.update((n) => Math.max(0, n + delta));
    this._feedCounts.update((list) =>
      list.map((f) =>
        f.feed_id === feedId ? { ...f, unread_count: Math.max(0, f.unread_count + delta) } : f,
      ),
    );
  }

  /** Optimistic single-article mark read: flips the row and the unread
   * counters immediately, rolls both back if the request fails. A no-op if
   * the article isn't in the currently loaded page (nothing to flip) or a
   * toggle for it is already in flight. */
  markRead(articleId: string, onError?: (err: unknown) => void): void {
    if (this.isPending(articleId)) return;
    const current = this._items().find((a) => a.id === articleId);
    if (!current || current.is_read) return;

    const requestedFor = this.loadedFor;
    this.setPending(articleId, true);
    this.patchItem(articleId, { is_read: true, read_at: new Date().toISOString() });
    this.adjustUnreadCount(current.feed_id, -1);

    this.me.markRead(articleId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.patchItem(articleId, { is_read: false, read_at: null });
        this.adjustUnreadCount(current.feed_id, 1);
        onError?.(err);
      },
    });
  }

  /** The unmark counterpart of markRead — see there. */
  markUnread(articleId: string, onError?: (err: unknown) => void): void {
    if (this.isPending(articleId)) return;
    const current = this._items().find((a) => a.id === articleId);
    if (!current || !current.is_read) return;

    const requestedFor = this.loadedFor;
    this.setPending(articleId, true);
    this.patchItem(articleId, { is_read: false, read_at: null });
    this.adjustUnreadCount(current.feed_id, 1);

    this.me.markUnread(articleId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.patchItem(articleId, { is_read: true, read_at: current.read_at });
        this.adjustUnreadCount(current.feed_id, -1);
        onError?.(err);
      },
    });
  }

  /**
   * Marks every currently-unread article among the ones already loaded on
   * screen as read (TODO.md "目前頁面全部標已讀") — sends the explicit id
   * list of unread rows in view, not a filter, so what gets marked read is
   * exactly what the reader can see right now.
   */
  markAllReadInView(onSuccess?: (marked: number) => void, onError?: (err: unknown) => void): void {
    const targets = this._items().filter((a) => !a.is_read);
    if (!targets.length) {
      onSuccess?.(0);
      return;
    }
    const requestedFor = this.loadedFor;
    const now = new Date().toISOString();
    this._items.update((items) =>
      items.map((a) => (a.is_read ? a : { ...a, is_read: true, read_at: now })),
    );
    for (const a of targets) this.adjustUnreadCount(a.feed_id, -1);

    // Batches aren't atomic as a set — one can commit while a later one
    // fails — so each batch's outcome is tracked independently instead of
    // blindly reverting every target on any single failure, which would
    // otherwise show server-confirmed reads as unread again.
    const batches = chunk(targets, MARK_ALL_BATCH_SIZE);
    forkJoin(
      batches.map((batch) =>
        this.me.markAllRead({ article_ids: batch.map((a) => a.id) }).pipe(
          map((result) => ({ ok: true as const, batch, marked: result.marked })),
          catchError((err: unknown) => of({ ok: false as const, batch, err })),
        ),
      ),
    ).subscribe((outcomes) => {
      if (this.loadedFor !== requestedFor) return;
      const failed = outcomes.filter((o) => !o.ok);
      const marked = outcomes.filter((o) => o.ok).reduce((sum, o) => sum + o.marked, 0);
      if (failed.length) {
        const failedIds = new Set(failed.flatMap((o) => o.batch.map((a) => a.id)));
        this._items.update((items) =>
          items.map((a) => (failedIds.has(a.id) ? { ...a, is_read: false, read_at: null } : a)),
        );
        for (const o of failed) for (const a of o.batch) this.adjustUnreadCount(a.feed_id, 1);
        onError?.(failed[0].err);
      }
      if (marked > 0 || !failed.length) onSuccess?.(marked);
    });
  }

  /**
   * Marks every article in an explicit *server-evaluated* scope as read
   * (TODO.md "明確範圍的全部標已讀") — optionally narrowed to one feed, not
   * limited to whatever happens to be loaded on screen. Not optimistic (the
   * scope can cover articles this page has never fetched, so there's no
   * local state to flip in advance); on success it locally marks any
   * already-loaded row the scope covers and refreshes the unread counts
   * from the server, which is authoritative either way.
   */
  markAllReadInScope(
    feedId: string | null,
    onSuccess?: (marked: number) => void,
    onError?: (err: unknown) => void,
  ): void {
    const requestedFor = this.loadedFor;
    this.me.markAllRead(feedId ? { feed_id: feedId } : {}).subscribe({
      next: (result) => {
        if (this.loadedFor !== requestedFor) return;
        const now = new Date().toISOString();
        this._items.update((items) =>
          items.map((a) =>
            !a.is_read && (!feedId || a.feed_id === feedId)
              ? { ...a, is_read: true, read_at: now }
              : a,
          ),
        );
        // The mark-all write itself already succeeded — surface a refresh
        // failure through the same onError channel so stale counts don't
        // linger silently, without implying the write itself failed.
        this.loadCounts(onError);
        onSuccess?.(result.marked);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        onError?.(err);
      },
    });
  }
}
