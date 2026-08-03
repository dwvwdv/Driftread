import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatPaginatorModule, PageEvent } from '@angular/material/paginator';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { FeedService } from '../../services/feed';
import { Feed, PaginatedFeeds } from '../../models';

@Component({
  selector: 'app-feed-list',
  imports: [
    RouterLink,
    FormsModule,
    MatCardModule,
    MatChipsModule,
    MatInputModule,
    MatFormFieldModule,
    MatSelectModule,
    MatButtonModule,
    MatPaginatorModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './feed-list.html',
  styleUrl: './feed-list.scss',
})
export class FeedList implements OnInit {
  private feedService = inject(FeedService);

  feeds = signal<Feed[]>([]);
  total = signal(0);
  loading = signal(false);
  error = signal('');

  page = signal(1);
  pageSize = signal(20);
  search = signal('');
  category = signal('');
  categories = signal<string[]>([]);

  ngOnInit(): void {
    this.loadCategories();
    this.loadFeeds();
  }

  loadCategories(): void {
    this.feedService.getCategories().subscribe({ next: (c) => this.categories.set(c) });
  }

  loadFeeds(): void {
    this.loading.set(true);
    this.error.set('');
    this.feedService
      .getFeeds(
        this.page(),
        this.pageSize(),
        this.category() || undefined,
        undefined,
        this.search() || undefined,
      )
      .subscribe({
        next: (res: PaginatedFeeds) => {
          this.feeds.set(res.items);
          this.total.set(res.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('載入失敗，請稍後再試。');
          this.loading.set(false);
        },
      });
  }

  onSearch(): void {
    this.page.set(1);
    this.loadFeeds();
  }

  onPage(event: PageEvent): void {
    this.page.set(event.pageIndex + 1);
    this.pageSize.set(event.pageSize);
    this.loadFeeds();
  }
}
