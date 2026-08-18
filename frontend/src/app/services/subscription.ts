import { Injectable, effect, inject, signal } from '@angular/core';
import { retry } from 'rxjs';
import { AuthService } from './auth';
import { MeService } from './me';
import { Feed } from '../models';

/**
 * Single source of truth for "is this feed subscribed", read by feed cards, the
 * feed detail page, and Discover alike.
 *
 * Before this, each page called `MeService.listSubscriptions()` and derived its
 * own local notion of subscribed/not — MyFeeds kept a `Feed[]`, and nothing told
 * FeedDetail or FeedList whether the feed they were showing was already
 * subscribed. Subscribing from one page never showed up on another without a
 * full reload.
 *
 * Loaded once per signed-in user (keyed on user id, same pattern as MyFeeds'
 * `loadedFor` — the session restores asynchronously, so this can't just run in a
 * constructor `if` and be done).
 */
@Injectable({ providedIn: 'root' })
export class SubscriptionService {
  private auth = inject(AuthService);
  private me = inject(MeService);

  private _ids = signal<ReadonlySet<string>>(new Set());
  private _loaded = signal(false);
  // A signal, not a plain Set: this app has no zone.js (see app.config.ts),
  // so nothing triggers change detection when an in-flight request settles
  // unless the mutation itself is a signal write. A plain Set here left
  // OnPush consumers' "pending" state (e.g. a disabled subscribe button)
  // stuck after the request that set it succeeded, until some unrelated
  // signal write elsewhere happened to repaint them.
  private _pending = signal<ReadonlySet<string>>(new Set());
  // What a write actually confirmed with the server (true = subscribed,
  // false = unsubscribed), kept past the moment the write leaves `_pending`
  // — needed because a GET /me/feeds snapshot can be *taken* before a
  // concurrent write commits but *arrive* after that write's own response
  // already cleared it from `_pending`.
  //
  // This can't just stay authoritative forever, though: a *later* write to
  // the same feed through some other path this service doesn't guard —
  // OPML import is the one that exists today — would never clear it, so a
  // fresh, genuinely up-to-date snapshot would keep losing to a
  // now-obsolete tombstone. `_confirmedAt` timestamps each entry with a
  // ticket from the same monotonic sequence `beginFetch()` draws from
  // (see below), and `sync()` only lets a confirmed value override a
  // snapshot whose *request* it can prove predates that confirmation —
  // arriving later is not the same thing as being taken later. Both maps
  // are cleared whenever the signed-in identity changes.
  private _confirmed = new Map<string, boolean>();
  private _confirmedAt = new Map<string, number>();
  // A single monotonic sequence, shared by every write commit (confirmWrite)
  // and every fetch-start (beginFetch): each draw is strictly greater than
  // every prior one, regardless of which of the two drew it. That's what
  // lets writes and snapshots be ordered against each other at all.
  private version = 0;
  // The highest asOf actually applied via sync() so far. A second, real
  // GET /me/feeds request can be in flight at once — SubscriptionService's
  // own load() and MyFeeds' independent fetch, say, racing around an OPML
  // import — and nothing about a single request's own asOf-vs-write
  // comparison stops an older-but-slower snapshot from landing after a
  // newer-but-faster one and clobbering it back. This rejects any sync()
  // whose asOf is older than one already applied, snapshot-vs-snapshot.
  private lastAppliedAsOf = -1;

  loaded = this._loaded.asReadonly();
  /**
   * Read-only view of the subscribed id set, for a caller that needs to
   * react to *any* change rather than poll `isSubscribed()` per feed — e.g.
   * MyFeeds dropping a feed from its own rendered list when it gets
   * unsubscribed from elsewhere while that page is open. Just the ids: this
   * service never holds full `Feed` objects, so a feed newly *subscribed*
   * elsewhere still needs an actual reload to have anything to render.
   */
  ids = this._ids.asReadonly();

  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      this.auth.session(); // establishes the reactive dependency
      this.syncIdentity();
    });
  }

  /**
   * Makes `loadedFor` (and, on an actual identity change, the rest of this
   * service's state) catch up with `auth.session()` right now, rather than
   * waiting for the constructor effect above to flush on Angular's own
   * schedule. Effects run scheduled, not synchronously with the signal
   * write that dirties them — Login calls this immediately after a
   * successful sign-in and before subscribing to a pending feed, so that
   * write is tagged with the identity it actually happens under instead of
   * whatever `loadedFor` still held from before the effect has caught up.
   */
  syncIdentity(): void {
    const userId = this.auth.session()?.user?.id ?? null;
    if (this.loadedFor === userId) return;
    this.loadedFor = userId;
    // Every identity change — signing out *or* switching straight to a
    // different account — starts from a clean slate. Without this, a
    // switch (no intervening null) kept the previous account's `_pending`
    // entries around; a stale write callback for the old account would
    // then either get dropped by the requestedFor guards below (leaving a
    // phantom "pending" id that was never the new account's to begin with)
    // or, worse, mutate state that now belongs to someone else.
    this._ids.set(new Set());
    this._pending.set(new Set());
    this._confirmed.clear();
    this._confirmedAt.clear();
    this._loaded.set(false);
    if (userId) this.load();
  }

  /**
   * Claims the next ticket in the shared write/fetch sequence — draw one
   * *before* issuing a request you'll later hand to `sync()` as `asOf`, so
   * ordering is judged against when the snapshot was actually taken, not
   * when its response happened to arrive.
   */
  beginFetch(): number {
    return ++this.version;
  }

  private confirmWrite(feedId: string, subscribed: boolean): void {
    const at = ++this.version;
    this._confirmed.set(feedId, subscribed);
    this._confirmedAt.set(feedId, at);
  }

  load(): void {
    // Captured so a response that outlives this user (signed out, or
    // switched accounts, before GET /me/feeds returned) gets dropped instead
    // of repopulating the cache with another user's subscriptions, or
    // clobbering the load a subsequent sign-in already kicked off.
    const requestedFor = this.loadedFor;
    const asOf = this.beginFetch();
    this.me
      .listSubscriptions()
      // A transient blip here otherwise leaves `loaded` false — and every
      // feed reading as "not subscribed" — for the rest of the session,
      // since nothing else ever retries: the identity effect only re-fires
      // on an actual user id change, not on a failed load for the same one.
      .pipe(retry({ count: 2, delay: 1000 }))
      .subscribe({
        next: (feeds) => {
          if (this.loadedFor !== requestedFor) return;
          this.sync(feeds, asOf);
        },
        error: () => {
          if (this.loadedFor !== requestedFor) return;
          // Leaves `loaded` false so callers keep treating subscription state
          // as unknown rather than confidently rendering "not subscribed" for
          // every feed.
          this._loaded.set(false);
        },
      });
  }

  /**
   * Reconciles with a `Feed[]` a caller already fetched for its own purposes
   * (MyFeeds needs full Feed objects, not just ids, to render its list) — so
   * that page becomes another writer of the single subscription cache instead
   * of a second GET /me/feeds request duplicating `load()`'s. Callers that
   * fetch this themselves (rather than going through `load()`) are
   * responsible for their own staleness check against the current user —
   * see MyFeeds.load() — since only they know which user their request was
   * actually for.
   *
   * `asOf` should be `beginFetch()` drawn *before* the caller issued its own
   * request; omitting it is only safe when the caller has no in-flight write
   * or competing fetch of its own to worry about racing against (or
   * genuinely doesn't care, e.g. in a test).
   */
  sync(feeds: Feed[], asOf: number = this.version): void {
    // A second GET can be in flight at once (see lastAppliedAsOf above); an
    // older-issued one arriving after a newer-issued one already landed is
    // moot — applying it now would only regress the cache.
    if (asOf < this.lastAppliedAsOf) return;
    this.lastAppliedAsOf = asOf;

    const serverIds = new Set(feeds.map((f) => f.id));
    // A write that is still in flight (e.g. one fired right after login, in
    // the same tick as the session change that triggered this reload) may
    // not be reflected in `feeds` yet — this snapshot can be a request that
    // started before that write committed. Keep the optimistic local value
    // for anything currently pending instead of letting a stale server
    // snapshot silently overwrite it.
    for (const id of this._pending()) {
      if (this._ids().has(id)) serverIds.add(id);
      else serverIds.delete(id);
    }
    // A write that already *finished* has the same problem once it is no
    // longer in `_pending` to protect it — but only if this snapshot's
    // request actually predates that confirmation (`asOf` before the write's
    // ticket). A confirmation *at or before* `asOf` is exactly what this
    // snapshot should already reflect (or a later state has since
    // superseded it through some other path — the snapshot wins either way).
    for (const [id, confirmed] of this._confirmed) {
      const writeVersion = this._confirmedAt.get(id) ?? 0;
      if (writeVersion <= asOf) continue;
      if (confirmed) serverIds.add(id);
      else serverIds.delete(id);
    }
    this._ids.set(serverIds);
    this._loaded.set(true);
  }

  isSubscribed(feedId: string): boolean {
    return this._ids().has(feedId);
  }

  /**
   * Records a subscription the backend already created as a side effect —
   * namely POST /discover/import auto-subscribing a signed-in importer — without
   * issuing a redundant POST /me/feeds/{id} of our own.
   */
  markSubscribed(feedId: string): void {
    this.confirmWrite(feedId, true);
    if (this.isSubscribed(feedId)) return;
    this._ids.set(new Set(this._ids()).add(feedId));
  }

  /** The unsubscribe counterpart of markSubscribed — see there. */
  markUnsubscribed(feedId: string): void {
    this.confirmWrite(feedId, false);
    if (!this.isSubscribed(feedId)) return;
    const next = new Set(this._ids());
    next.delete(feedId);
    this._ids.set(next);
  }

  isPending(feedId: string): boolean {
    return this._pending().has(feedId);
  }

  private setPending(feedId: string, pending: boolean): void {
    const next = new Set(this._pending());
    if (pending) next.add(feedId);
    else next.delete(feedId);
    this._pending.set(next);
  }

  /**
   * True if some *other* write already confirmed a result for this feed
   * after `sinceTicket` — meaning our own rollback below would be
   * second-guessing a truth that's actually newer than the attempt that's
   * failing, e.g. MyFeeds' own independent `MeService.unsubscribe()` for the
   * same feed succeeding while this attempt was still in flight.
   */
  private confirmedSince(feedId: string, sinceTicket: number): boolean {
    return (this._confirmedAt.get(feedId) ?? 0) > sinceTicket;
  }

  /**
   * Optimistic subscribe: flips local state immediately, rolls back on
   * failure. A feed already pending or already subscribed is a no-op —
   * guards against a double-click firing the request twice before the first
   * response lands. `onSuccess` matters when a caller has its own state tied
   * to the subscription actually landing (e.g. Recommendations recording the
   * feed as liked and advancing its deck only once this is confirmed, not
   * the moment the optimistic update fires).
   */
  subscribe(feedId: string, onError?: (err: unknown) => void, onSuccess?: () => void): void {
    if (this.isPending(feedId) || this.isSubscribed(feedId)) return;
    // Captured for the same reason as load()'s requestedFor: if the signed-in
    // user changes before this settles, the constructor effect has already
    // reset _ids/_pending for the new identity — applying this response on
    // top of that would mutate state that is no longer this write's to touch
    // (a stray add, a stray rollback-delete, or a phantom stuck-pending id).
    const requestedFor = this.loadedFor;
    // A plain read, not beginFetch(): only needs to mark "as of right now",
    // for confirmedSince() to compare against below — nothing else needs to
    // reference this specific moment, so it doesn't need its own ticket.
    const attemptAt = this.version;
    this.setPending(feedId, true);
    this._ids.set(new Set(this._ids()).add(feedId));

    this.me.subscribe(feedId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(feedId, false);
        this.confirmWrite(feedId, true);
        onSuccess?.();
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(feedId, false);
        if (!this.confirmedSince(feedId, attemptAt)) {
          const next = new Set(this._ids());
          next.delete(feedId);
          this._ids.set(next);
        }
        onError?.(err);
      },
    });
  }

  unsubscribe(feedId: string, onError?: (err: unknown) => void): void {
    if (this.isPending(feedId) || !this.isSubscribed(feedId)) return;
    const requestedFor = this.loadedFor;
    const attemptAt = this.version; // see subscribe()'s attemptAt
    this.setPending(feedId, true);
    const next = new Set(this._ids());
    next.delete(feedId);
    this._ids.set(next);

    this.me.unsubscribe(feedId).subscribe({
      next: () => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(feedId, false);
        this.confirmWrite(feedId, false);
      },
      error: (err: unknown) => {
        if (this.loadedFor !== requestedFor) return;
        this.setPending(feedId, false);
        if (!this.confirmedSince(feedId, attemptAt)) {
          this._ids.set(new Set(this._ids()).add(feedId));
        }
        onError?.(err);
      },
    });
  }
}
