import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { DiscoverService } from '../../services/discover';
import { AuthService } from '../../services/auth';
import { DiscoveredFeed } from '../../models';
import { apiMessage, isRateLimited, retryAfterSeconds } from '../../shared/http-errors';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';

/**
 * Paste a URL, get the RSS/Atom feeds behind it.
 *
 * POST /api/discover and /discover/import are both rate limited at 20 requests per
 * 60 seconds per IP, so a 429 is told as a countdown rather than as "發現失敗".
 */
@Component({
  selector: 'app-discover',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, FormsModule, ObIcon, ObLoading, ObError, ObEmpty, ObPageHeader],
  templateUrl: './discover.html',
  styleUrl: './discover.scss',
})
export class Discover {
  protected auth = inject(AuthService);
  private discoverService = inject(DiscoverService);

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
    this.importing.set(candidate.feed_url);
    this.error.set('');

    this.discoverService.importByUrl(candidate.feed_url).subscribe({
      next: () => {
        this.importing.set(null);
        this.imported.update((set) => new Set(set).add(candidate.feed_url));
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

  private startCooldown(seconds: number): void {
    this.cooldown.set(seconds);
    const timer = setInterval(() => {
      this.cooldown.update((n) => n - 1);
      if (this.cooldown() <= 0) clearInterval(timer);
    }, 1000);
  }
}
