import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatListModule } from '@angular/material/list';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { DatePipe } from '@angular/common';
import { FeedService } from '../../services/feed';
import { RecommendationService } from '../../services/recommendation';
import { FeedWithArticles } from '../../models';

@Component({
  selector: 'app-feed-detail',
  imports: [
    RouterLink, DatePipe,
    MatCardModule, MatButtonModule, MatChipsModule,
    MatListModule, MatDividerModule, MatProgressSpinnerModule, MatIconModule,
  ],
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

  get feedId(): string { return this.route.snapshot.paramMap.get('id') ?? ''; }

  get isLiked(): boolean { return this.rec.liked().includes(this.feedId); }
  get isDisliked(): boolean { return this.rec.disliked().includes(this.feedId); }

  ngOnInit(): void {
    this.feedService.getFeed(this.feedId).subscribe({
      next: f => { this.feed.set(f); this.loading.set(false); },
      error: () => { this.error.set('無法載入信息源。'); this.loading.set(false); },
    });
  }

  like(): void { this.rec.like(this.feedId); }
  dislike(): void { this.rec.dislike(this.feedId); }
}
