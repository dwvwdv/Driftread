import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FeedService } from '../../services/feed';
import { RecommendationService } from '../../services/recommendation';
import { FeedWithArticles } from '../../models';
import { ObIcon } from '../../ui/icon/icon';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';

/** One feed: its metadata, a like/dislike control, and its latest articles. */
@Component({
  selector: 'app-feed-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObIcon, ObListRow, ObLoading, ObError, ObEmpty],
  templateUrl: './feed-detail.html',
  styleUrl: './feed-detail.scss',
})
export class FeedDetail implements OnInit {
  private route = inject(ActivatedRoute);
  private feedService = inject(FeedService);
  private rec = inject(RecommendationService);

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
}
