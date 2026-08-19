import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FeedService } from '../../services/feed';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { FeedWithArticles } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { ObIcon } from '../../ui/icon/icon';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ToastService } from '../../ui/toast/toast';

/** One feed: its metadata, subscribe/like/dislike controls, and its latest articles. */
@Component({
  selector: 'app-feed-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObIcon, ObListRow, ObLoading, ObError, ObEmpty],
  templateUrl: './feed-detail.html',
  styleUrl: './feed-detail.scss',
})
export class FeedDetail implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private feedService = inject(FeedService);
  private rec = inject(RecommendationService);
  private subs = inject(SubscriptionService);
  protected auth = inject(AuthService);
  private toast = inject(ToastService);

  feed = signal<FeedWithArticles | null>(null);
  loading = signal(true);
  error = signal('');

  get feedId(): string {
    return this.route.snapshot.paramMap.get('id') ?? '';
  }

  get isLiked(): boolean {
    return this.rec.liked().includes(this.feedId);
  }

  get isDisliked(): boolean {
    return this.rec.disliked().includes(this.feedId);
  }

  get isSubscribed(): boolean {
    return this.subs.isSubscribed(this.feedId);
  }

  get subscribePending(): boolean {
    return this.subs.isPending(this.feedId);
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set('');
    this.feedService.getFeed(this.feedId).subscribe({
      next: (f) => {
        this.feed.set(f);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('無法載入信息源。');
        this.loading.set(false);
      },
    });
  }

  like(): void {
    this.rec.like(this.feedId);
  }

  dislike(): void {
    this.rec.dislike(this.feedId);
  }

  /**
   * Not signed in: sends the reader to log in first, then straight back to this
   * feed with the subscription completed — rather than dropping the click
   * entirely or landing them on the home page with no memory of what they meant
   * to do.
   */
  toggleSubscribe(): void {
    const feedId = this.feedId;
    if (!this.auth.session()) {
      void this.router.navigate(['/login'], {
        queryParams: { redirect: `/feeds/${feedId}`, subscribeFeed: feedId },
      });
      return;
    }

    if (this.isSubscribed) {
      this.subs.unsubscribe(feedId, (err) => this.toast.danger(apiMessage(err, '取消訂閱失敗')));
    } else {
      this.subs.subscribe(feedId, (err) => this.toast.danger(apiMessage(err, '訂閱失敗')));
    }
  }
}
