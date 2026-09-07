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
 * comment).
 *
 * Like SubscriptionService, every fetch and every write draws a ticket from
 * one shared monotonic `version` sequence (`beginFetch()` / a write's commit
 * point), so a GET and a concurrent write on the same article or the same
 * counts can be ordered against each other. Three races this guards against
 * (all previously open gaps — see TODO.md "技術與可靠性優化"):
 *
 * - `load()`/`loadMore()` reconciling a fetched page against `_pending`
 *   (a still-in-flight markRead/markUnread wins over the stale snapshot)
 *   and `_confirmedRead`/`_confirmedReadAt` (a write that committed *after*
 *   the GET started wins even once it's no longer `_pending`) — mirrors
 *   SubscriptionService's `sync()` exactly, just per-article instead of a
 *   single id set.
 * - Unread counts are a baseline (the last accepted GET) plus a ledger of
 *   deltas (`_countDeltas`) not yet known to be reflected by it — a GET
 *   response is *applied*, never wholesale discarded, with any delta
 *   committed after that GET started re-added on top. The previous
 *   generation-counter version discarded a stale-relative-to-a-local-write
 *   response outright, including on the very first load — a markRead/
 *   markUnread landing before that first response ever arrives left the
 *   total stuck at whatever the clamped optimistic guess was, forever,
 *   since nothing else ever retries once `countsLoaded` flips true.
 * - `markAllReadInView` now claims its targets through the same `_pending`
 *   set `markRead`/`markUnread` check, and vice versa — previously a batch
 *   mark-all and a per-article toggle for the same article could both
 *   apply their own optimistic count delta for what is, server-side, the
 *   same single change, double-counting it.
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
   * disabled state instead of racing a double-click against itself, and
   * (since `markAllReadInView` claims the same set for its targets) keeps a
   * batch mark-all and a single-article toggle from both touching the same
   * article at once. */
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

  /** Single monotonic sequence shared by every fetch-start (`beginFetch()`)
   * and every write commit, exactly like SubscriptionService's `version` —
   * what lets a GET and a write be ordered against each other regardless of
   * which one's response/confirmation actually arrives first. */
  private version = 0;

  /** What a markRead/markUnread/markAllReadInView write actually confirmed
   * for an article, kept past the moment it leaves `_pending` — needed
   * because a `load()`/`loadMore()` GET can be *issued* before that write
   * commits but *arrive* after, in which case its snapshot must not win.
   * Cleared on every identity change. */
  private _confirmedRead = new Map<string, { is_read: boolean; read_at: string | null }>();
  private _confirmedReadAt = new Map<string, number>();

  /** Last accepted GET /me/stream/unread-counts snapshot and the ticket it
   * was issued at, plus every local count delta not yet known to be
   * reflected by it. Displayed totals are always `recomputeCounts()`'s
   * baseline-plus-outstanding-deltas, never the raw baseline or the raw
   * delta sum alone — see this class's header comment. */
  private _countsBaselineTotal = 0;
  private _countsBaselineFeeds: FeedUnreadCount[] = [];
  private _countsBaselineAsOf = -1;
  private _countDeltas: { ticket: number; feedId: string; amount: number }[] = [];

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
      this._confirmedRead.clear();
      this._confirmedReadAt.clear();
      this._countsBaselineTotal = 0;
      this._countsBaselineFeeds = [];
      this._countsBaselineAsOf = -1;
      this._countDeltas = [];
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

  /** Claims the next ticket in the shared fetch/write sequence — draw one
   * *before* issuing a request whose ordering later needs to be judged
   * against a write, exactly like SubscriptionService's `beginFetch()`. */
  private beginFetch(): number {
    return ++this.version;
  }

  private confirmReadState(articleId: string, isRead: boolean, readAt: string | null): void {
    const ticket = ++this.version;
    this._confirmedRead.set(articleId, { is_read: isRead, read_at: readAt });
    this._confirmedReadAt.set(articleId, ticket);
  }

  /**
   * Reconciles a freshly fetched page against in-flight and recently
   * confirmed per-article writes, mirroring SubscriptionService.sync():
   * a `_pending` article keeps whatever `previous` (the items array right
   * before this response landed) already showed for it — the write hasn't
   * settled, so there's nothing authoritative to overwrite it with yet —
   * and a confirmed write with a ticket *after* this GET's `asOf` wins over
   * the fetched value, since the GET may predate that confirmation.
   */
  private reconcileItems(
    incoming: StreamArticle[],
    asOf: number,
    previous: readonly StreamArticle[],
  ): StreamArticle[] {
    const pending = this._pending();
    return incoming.map((item) => {
      if (pending.has(item.id)) {
        const prior = previous.find((a) => a.id === item.id);
        if (prior) return prior;
      }
      const confirmedAt = this._confirmedReadAt.get(item.id);
      if (confirmedAt !== undefined && confirmedAt > asOf) {
        const confirmed = this._confirmedRead.get(item.id);
        if (confirmed) return { ...item, is_read: confirmed.is_read, read_at: confirmed.read_at };
      }
      return item;
    });
  }

  loadCounts(onError?: (err: unknown) => void): void {
    const requestedFor = this.loadedFor;
    const asOf = this.beginFetch();
    this._countsLoading.set(true);
    this.me.getUnreadCounts().subscribe({
      next: (summary) => {
        if (this.loadedFor !== requestedFor) return;
        this._countsLoading.set(false);
        // A newer loadCounts() (higher-ticketed) already landed and applied
        // its own baseline — this response is stale relative to it, not
        // relative to any local write, so it's simply moot.
        if (asOf < this._countsBaselineAsOf) return;
        this._countsBaselineTotal = summary.total_unread;
        this._countsBaselineFeeds = summary.feeds;
        this._countsBaselineAsOf = asOf;
        // Deltas at or before this GET's ticket are presumed already baked
        // into `summary` — anything after is a write this snapshot can't
        // have seen yet and must be re-applied on top.
        this._countDeltas = this._countDeltas.filter((d) => d.ticket > asOf);
        this.recomputeCounts();
        this._countsLoaded.set(true);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this._countsLoading.set(false);
        if (asOf < this._countsBaselineAsOf) return;
        onError?.(err);
      },
    });
  }

  /** Replaces `items` with the first page for the given filters. */
  load(filters: StreamFilters, onError?: (err: unknown) => void): void {
    const requestedFor = this.loadedFor;
    const generation = ++this._itemsGeneration;
    const asOf = this.beginFetch();
    const previous = this._items();
    this._loading.set(true);
    // A fresh load supersedes any load-more in flight for the previous
    // generation — that request's own callback will now bail out on the
    // generation check below without ever clearing this flag itself.
    this._loadingMore.set(false);
    this.me.getStream({ feedId: filters.feedId, unreadOnly: filters.unreadOnly }).subscribe({
      next: (page) => {
        if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
        this._loading.set(false);
        this._items.set(this.reconcileItems(page.items, asOf, previous));
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
    const asOf = this.beginFetch();
    const previous = this._items();
    this._loadingMore.set(true);
    this.me
      .getStream({ cursor, feedId: filters.feedId, unreadOnly: filters.unreadOnly })
      .subscribe({
        next: (page) => {
          if (this.loadedFor !== requestedFor || generation !== this._itemsGeneration) return;
          this._loadingMore.set(false);
          const reconciled = this.reconcileItems(page.items, asOf, previous);
          this._items.update((current) => [...current, ...reconciled]);
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

  /** Recomputes the displayed totals from the last accepted baseline plus
   * every outstanding delta committed after it — see this class's header
   * comment. Deltas for a feed absent from the baseline (shouldn't happen
   * in practice — a delta always originates from an article already in
   * `_feedCounts`) are simply dropped, same as the old code's `.map()`
   * silently no-oping on an unmatched `feed_id`. */
  private recomputeCounts(): void {
    const perFeed = new Map(this._countsBaselineFeeds.map((f) => [f.feed_id, { ...f }]));
    let total = this._countsBaselineTotal;
    for (const d of this._countDeltas) {
      if (d.ticket <= this._countsBaselineAsOf) continue;
      total += d.amount;
      const entry = perFeed.get(d.feedId);
      if (entry) entry.unread_count += d.amount;
    }
    this._totalUnread.set(Math.max(0, total));
    this._feedCounts.set(
      [...perFeed.values()].map((f) => ({ ...f, unread_count: Math.max(0, f.unread_count) })),
    );
  }

  private commitCountDelta(feedId: string, amount: number): void {
    const ticket = ++this.version;
    this._countDeltas.push({ ticket, feedId, amount });
    this.recomputeCounts();
  }

  /** Optimistic single-article mark read: flips the row and the unread
   * counters immediately, rolls both back if the request fails. A no-op if
   * the article isn't in the currently loaded page (nothing to flip) or a
   * toggle for it — or a batch mark-all covering it — is already in
   * flight. */
  markRead(articleId: string, onError?: (err: unknown) => void): void {
    if (this.isPending(articleId)) return;
    const current = this._items().find((a) => a.id === articleId);
    if (!current || current.is_read) return;

    const requestedFor = this.loadedFor;
    this.setPending(articleId, true);
    const readAt = new Date().toISOString();
    this.patchItem(articleId, { is_read: true, read_at: readAt });
    this.commitCountDelta(current.feed_id, -1);

    this.me.markRead(articleId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.confirmReadState(articleId, true, readAt);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.patchItem(articleId, { is_read: false, read_at: null });
        this.commitCountDelta(current.feed_id, 1);
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
    this.commitCountDelta(current.feed_id, 1);

    this.me.markUnread(articleId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.confirmReadState(articleId, false, null);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(articleId, false);
        this.patchItem(articleId, { is_read: true, read_at: current.read_at });
        this.commitCountDelta(current.feed_id, -1);
        onError?.(err);
      },
    });
  }

  /**
   * Marks every currently-unread, not-already-claimed article among the
   * ones already loaded on screen as read (TODO.md "目前頁面全部標已讀") —
   * sends the explicit id list of unread rows in view, not a filter, so
   * what gets marked read is exactly what the reader can see right now.
   * An article with its own markRead/markUnread (or another mark-all) still
   * in flight is left for that write to settle rather than claimed here too
   * — claiming it here as well would apply two independent optimistic
   * count deltas for what is, server-side, the same single change.
   */
  markAllReadInView(onSuccess?: (marked: number) => void, onError?: (err: unknown) => void): void {
    const targets = this._items().filter((a) => !a.is_read && !this.isPending(a.id));
    if (!targets.length) {
      onSuccess?.(0);
      return;
    }
    const requestedFor = this.loadedFor;
    const now = new Date().toISOString();
    const targetIds = new Set(targets.map((a) => a.id));
    this._pending.update((p) => new Set([...p, ...targetIds]));
    this._items.update((items) =>
      items.map((a) => (targetIds.has(a.id) ? { ...a, is_read: true, read_at: now } : a)),
    );
    for (const a of targets) this.commitCountDelta(a.feed_id, -1);

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
      this._pending.update((p) => {
        const next = new Set(p);
        for (const id of targetIds) next.delete(id);
        return next;
      });
      const failed = outcomes.filter((o) => !o.ok);
      const marked = outcomes.filter((o) => o.ok).reduce((sum, o) => sum + o.marked, 0);
      if (failed.length) {
        const failedIds = new Set(failed.flatMap((o) => o.batch.map((a) => a.id)));
        this._items.update((items) =>
          items.map((a) => (failedIds.has(a.id) ? { ...a, is_read: false, read_at: null } : a)),
        );
        for (const o of failed) for (const a of o.batch) this.commitCountDelta(a.feed_id, 1);
        onError?.(failed[0].err);
      }
      for (const o of outcomes) {
        if (!o.ok) continue;
        for (const a of o.batch) this.confirmReadState(a.id, true, now);
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
   * already-loaded row the scope covers — skipping one with its own write
   * still in flight, same reasoning as markAllReadInView — and refreshes
   * the unread counts from the server, which is authoritative either way.
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
        const affected: string[] = [];
        this._items.update((items) =>
          items.map((a) => {
            if (a.is_read || (feedId && a.feed_id !== feedId) || this.isPending(a.id)) return a;
            affected.push(a.id);
            return { ...a, is_read: true, read_at: now };
          }),
        );
        for (const id of affected) this.confirmReadState(id, true, now);
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
