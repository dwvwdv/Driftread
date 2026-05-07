import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { RecommendationService } from '../../services/recommendation';
import { Feed } from '../../models';

@Component({
  selector: 'app-recommendations',
  imports: [RouterLink, MatCardModule, MatButtonModule, MatChipsModule, MatProgressSpinnerModule, MatIconModule],
  templateUrl: './recommendations.html',
  styleUrl: './recommendations.scss',
})
export class Recommendations implements OnInit {
  private rec = inject(RecommendationService);

  feeds = signal<Feed[]>([]);
  loading = signal(true);
  error = signal('');
  currentIndex = signal(0);

  get current(): Feed | null {
    const list = this.feeds();
    const idx = this.currentIndex();
    return idx < list.length ? list[idx] : null;
  }

  get likedCount(): number { return this.rec.liked().length; }

  ngOnInit(): void { this.loadMore(); }

  loadMore(): void {
    this.loading.set(true);
    this.error.set('');
    this.rec.getRecommendations(15).subscribe({
      next: feeds => {
        this.feeds.set(feeds);
        this.currentIndex.set(0);
        this.loading.set(false);
      },
      error: () => { this.error.set('無法載入推薦。'); this.loading.set(false); },
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

  next(): void {
    const nextIdx = this.currentIndex() + 1;
    if (nextIdx >= this.feeds().length) {
      this.loadMore();
    } else {
      this.currentIndex.set(nextIdx);
    }
  }
}
