import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { RecommendationService } from '../../services/recommendation';
import { Feed } from '../../models';
import { isRateLimited, retryAfterSeconds } from '../../shared/http-errors';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';

/**
 * 猜你喜歡 — one card at a time, like or skip.
 *
 * GET /api/recommendations is rate limited to 20 requests per 60 seconds per IP,
 * and that bucket is shared by everyone behind the same NAT. Two changes here fall
 * straight out of that:
 *
 *  - The deck no longer refetches implicitly. next() used to call loadMore() the
 *    moment the 15th card was passed, so anyone clicking quickly burned the
 *    minute's quota in well under a minute and then hit 429s with no explanation.
 *    The deck is now 50 (the server's own maximum) and refilling is an explicit
 *    action.
 *
 *  - A 429 is handled rather than surfacing as "無法載入推薦". Retry-After is read
 *    off the response and counted down, so the reader knows it is a wait and not a
 *    breakage.
 */
@Component({
  selector: 'app-recommendations',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, ObIcon, ObLoading, ObError, ObEmpty, ObPageHeader],
  templateUrl: './recommendations.html',
  styleUrl: './recommendations.scss',
})
export class Recommendations implements OnInit, OnDestroy {
  private rec = inject(RecommendationService);

  /** The server's own cap, so one fetch lasts as long as possible. */
  private static readonly DECK_SIZE = 50;

  feeds = signal<Feed[]>([]);
  loading = signal(true);
  error = signal('');
  currentIndex = signal(0);
  cooldown = signal(0);

  private timer: ReturnType<typeof setInterval> | null = null;

  get current(): Feed | null {
    const list = this.feeds();
    const index = this.currentIndex();
    return index < list.length ? (list[index] ?? null) : null;
  }

  get likedCount(): number {
    return this.rec.liked().length;
  }

  get remaining(): number {
    return Math.max(0, this.feeds().length - this.currentIndex());
  }

  ngOnInit(): void {
    this.loadMore();
  }

  ngOnDestroy(): void {
    this.stopCooldown();
  }

  loadMore(): void {
    if (this.cooldown() > 0) return;

    this.loading.set(true);
    this.error.set('');
    this.rec.getRecommendations(Recommendations.DECK_SIZE).subscribe({
      next: (feeds) => {
        this.feeds.set(feeds);
        this.currentIndex.set(0);
        this.loading.set(false);
      },
      error: (e: unknown) => {
        this.loading.set(false);
        if (isRateLimited(e)) {
          this.startCooldown(retryAfterSeconds(e));
          return;
        }
        this.error.set('無法載入推薦，請稍後再試。');
      },
    });
  }

  like(feed: Feed): void {
    this.rec.like(feed.id);
    this.next();
  }

  skip(feed: Feed): void {
    this.rec.dislike(feed.id);
    this.next();
  }

  /**
   * Advances only. Running off the end shows the empty state and its refill
   * button rather than silently firing another request.
   */
  next(): void {
    this.currentIndex.update((index) => index + 1);
  }

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
    this.cooldown.set(0);
  }
}
