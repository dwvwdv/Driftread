import { ChangeDetectionStrategy, Component, OnDestroy, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { DiscoverService } from '../../services/discover';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { DiscoveredFeed } from '../../models';
import { apiMessage, isRateLimited, retryAfterSeconds } from '../../shared/http-errors';
import { setPendingImportFeedUrl } from '../../shared/pending-import';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ToastService } from '../../ui/toast/toast';

/**
 * Paste a URL, get the RSS/Atom feeds behind it.
 *
 * POST /api/discover and /discover/import are both rate limited at 20 requests per
 * 60 seconds per IP, so a 429 is told as a countdown rather than as "發現失敗".
 *
 * Discovering candidates works signed out; importing one requires a signed-in
 * caller (docs/SECURITY.md #30 — /discover/import writes third-party metadata
 * into the global feeds catalog), same as subscribing to an already-known feed.
 */
@Component({
  selector: 'app-discover',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, FormsModule, ObIcon, ObLoading, ObError, ObEmpty, ObPageHeader],
  templateUrl: './discover.html',
  styleUrl: './discover.scss',
})
export class Discover implements OnDestroy {
  protected auth = inject(AuthService);
  private router = inject(Router);
  private discoverService = inject(DiscoverService);
  private subs = inject(SubscriptionService);
  private toast = inject(ToastService);

  url = '';
  busy = signal(false);
  error = signal('');
  cooldown = signal(0);
  candidates = signal<DiscoveredFeed[] | null>(null);
  importing = signal<string | null>(null);

  /**
   * Feed URLs imported during this visit.
   *
   * Tracked separately rather than by flipping `already_exists` on the response
   * object, so the rendered list stays a faithful record of what the server
   * actually said.
   */
  imported = signal<ReadonlySet<string>>(new Set());

  private timer: ReturnType<typeof setInterval> | null = null;

  ngOnDestroy(): void {
    this.stopCooldown();
  }

  run(): void {
    const url = this.url.trim();
    if (!url || this.cooldown() > 0) return;

    this.busy.set(true);
    this.error.set('');
    this.candidates.set(null);

    this.discoverService.discover(url).subscribe({
      next: (result) => {
        this.candidates.set(result.candidates);
        this.busy.set(false);
      },
      error: (e: unknown) => {
        this.busy.set(false);
        if (isRateLimited(e)) {
          this.startCooldown(retryAfterSeconds(e));
          return;
        }
        this.error.set(apiMessage(e, '發現失敗'));
      },
    });
  }

  importFeed(candidate: DiscoveredFeed): void {
    // POST /discover/import now requires a signed-in caller (docs/SECURITY.md
    // #30): it writes third-party-supplied metadata straight into the global
    // feeds catalog, and rate limiting alone doesn't stop pollution across
    // rotated IPs. Same redirect-to-login shape as subscribeExisting(), plus
    // stashing the URL so Login.submit() can resume the import instead of
    // dropping the reader back on a blank form (Codex review on PR #52) — in
    // sessionStorage rather than a query param, so a crafted login link can't
    // trigger an import the reader never asked for; the nonce it returns ties
    // resumption to *this* redirect, not to any later, unrelated login
    // (see pending-import.ts).
    if (!this.auth.session()) {
      const importNonce = setPendingImportFeedUrl(candidate.feed_url);
      void this.router.navigate(['/login'], {
        queryParams: { redirect: '/discover', importNonce },
      });
      return;
    }

    this.importing.set(candidate.feed_url);
    this.error.set('');
    // Captured so a response arriving after a sign-out/account switch can't
    // credit whoever is signed in *now* with a subscription the backend
    // actually created for whoever was signed in when the import was sent —
    // same class of bug as SubscriptionService.subscribe()'s own guard.
    const requestedFor = this.auth.session()?.user?.id ?? null;

    this.discoverService.importByUrl(candidate.feed_url).subscribe({
      next: (feed) => {
        this.importing.set(null);
        this.imported.update((set) => new Set(set).add(candidate.feed_url));
        // POST /discover/import auto-subscribes the importer server-side
        // (backend/routers/discover.py); this just keeps SubscriptionService's
        // cache in sync so the feed shows as subscribed elsewhere without a
        // reload.
        if (requestedFor && (this.auth.session()?.user?.id ?? null) === requestedFor) {
          this.subs.markSubscribed(feed.id);
        }
      },
      error: (e: unknown) => {
        this.importing.set(null);
        if (isRateLimited(e)) {
          this.startCooldown(retryAfterSeconds(e));
          return;
        }
        this.error.set(apiMessage(e, '匯入失敗'));
      },
    });
  }

  isImported(candidate: DiscoveredFeed): boolean {
    return candidate.already_exists || this.imported().has(candidate.feed_url);
  }

  isSubscribed(feedId: string): boolean {
    return this.subs.isSubscribed(feedId);
  }

  isSubscribePending(feedId: string): boolean {
    return this.subs.isPending(feedId);
  }

  /**
   * For a candidate already in the catalogue: subscribes directly instead of
   * only offering "前往查看" — the reader already told us they want this feed by
   * pasting a URL that led to it.
   */
  subscribeExisting(feedId: string): void {
    if (!this.auth.session()) {
      void this.router.navigate(['/login'], {
        queryParams: { redirect: '/discover', subscribeFeed: feedId },
      });
      return;
    }
    this.subs.subscribe(feedId, (err) => this.toast.danger(apiMessage(err, '訂閱失敗')));
  }

  /**
   * Host as the browser parses it, shown next to the remote-supplied title.
   *
   * Same reasoning as the admin candidate queue (docs/SECURITY.md rule 10): the
   * title comes from a third party and can be crafted to look like it belongs to
   * a domain it does not.
   */
  hostOf(feedUrl: string): string {
    try {
      return new URL(feedUrl).host;
    } catch {
      return feedUrl;
    }
  }

  /**
   * Starts (or restarts) the rate-limit countdown.
   *
   * Only ever one timer. Imports fire per candidate, so several can be in flight
   * and each 429 lands here — a fresh interval per response would decrement the
   * same signal two or three times a second, releasing the button well before
   * Retry-After has actually elapsed and earning another 429 immediately.
   */
  private startCooldown(seconds: number): void {
    this.stopCooldown();
    this.cooldown.set(seconds);
    this.timer = setInterval(() => {
      this.cooldown.update((n) => n - 1);
      if (this.cooldown() <= 0) this.stopCooldown();
    }, 1000);
  }

  private stopCooldown(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
