import { Injectable, effect, inject, signal } from '@angular/core';
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

  loaded = this._loaded.asReadonly();

  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        this._ids.set(new Set());
        this._loaded.set(false);
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.load();
    });
  }

  load(): void {
    // Captured so a response that outlives this user (signed out, or
    // switched accounts, before GET /me/feeds returned) gets dropped instead
    // of repopulating the cache with another user's subscriptions, or
    // clobbering the load a subsequent sign-in already kicked off.
    const requestedFor = this.loadedFor;
    this.me.listSubscriptions().subscribe({
      next: (feeds) => {
        if (this.loadedFor !== requestedFor) return;
        this.sync(feeds);
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
   * of a second GET /me/feeds request duplicating `load()`'s.
   */
  sync(feeds: Feed[]): void {
    const serverIds = new Set(feeds.map((f) => f.id));
    // A subscribe/unsubscribe that is still in flight (e.g. one fired right
    // after login, in the same tick as the session change that triggered this
    // reload) may not be reflected in `feeds` yet — this snapshot can be a
    // request that started before that write committed. For anything
    // currently pending, keep the optimistic local value instead of letting a
    // stale server snapshot silently overwrite it; everything else takes the
    // server's word.
    for (const id of this._pending()) {
      if (this._ids().has(id)) serverIds.add(id);
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
    if (this.isSubscribed(feedId)) return;
    this._ids.set(new Set(this._ids()).add(feedId));
  }

  /** The unsubscribe counterpart of markSubscribed — see there. */
  markUnsubscribed(feedId: string): void {
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
    this.setPending(feedId, true);
    this._ids.set(new Set(this._ids()).add(feedId));

    this.me.subscribe(feedId).subscribe({
      next: () => {
        this.setPending(feedId, false);
        onSuccess?.();
      },
      error: (err: unknown) => {
        this.setPending(feedId, false);
        const next = new Set(this._ids());
        next.delete(feedId);
        this._ids.set(next);
        onError?.(err);
      },
    });
  }

  unsubscribe(feedId: string, onError?: (err: unknown) => void): void {
    if (this.isPending(feedId) || !this.isSubscribed(feedId)) return;
    this.setPending(feedId, true);
    const next = new Set(this._ids());
    next.delete(feedId);
    this._ids.set(next);

    this.me.unsubscribe(feedId).subscribe({
      next: () => this.setPending(feedId, false),
      error: (err: unknown) => {
        this.setPending(feedId, false);
        this._ids.set(new Set(this._ids()).add(feedId));
        onError?.(err);
      },
    });
  }
}
