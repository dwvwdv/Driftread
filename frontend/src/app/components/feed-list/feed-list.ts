import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { FeedService } from '../../services/feed';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { Feed, PaginatedFeeds } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ObPaginator } from '../../ui/paginator/paginator';
import { ToastService } from '../../ui/toast/toast';

/** The feed catalogue. */
@Component({
  selector: 'app-feed-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    FormsModule,
    ObIcon,
    ObLoading,
    ObError,
    ObEmpty,
    ObPageHeader,
    ObPaginator,
  ],
  templateUrl: './feed-list.html',
  styleUrl: './feed-list.scss',
})
export class FeedList implements OnInit {
  private feedService = inject(FeedService);
  private router = inject(Router);
  private subs = inject(SubscriptionService);
  protected auth = inject(AuthService);
  private toast = inject(ToastService);

  feeds = signal<Feed[]>([]);
  total = signal(0);
  loading = signal(false);
  error = signal('');

  page = signal(1);
  pageSize = signal(20);
  search = '';
  category = '';
  categories = signal<string[]>([]);

  ngOnInit(): void {
    this.loadCategories();
    this.loadFeeds();
  }

  loadCategories(): void {
    this.feedService.getCategories().subscribe({
      next: (c) => this.categories.set(c),
      // A missing category list degrades the filter to "全部" but leaves the
      // catalogue itself perfectly usable, so it does not surface as a page error.
      error: () => this.categories.set([]),
    });
  }

  loadFeeds(): void {
    this.loading.set(true);
    this.error.set('');
    this.feedService
      .getFeeds(
        this.page(),
        this.pageSize(),
        this.category || undefined,
        undefined,
        this.search || undefined,
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

  onPage(page: number): void {
    this.page.set(page);
    this.loadFeeds();
  }

  onPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
    this.loadFeeds();
  }

  clearFilters(): void {
    this.search = '';
    this.category = '';
    this.onSearch();
  }

  get hasFilters(): boolean {
    return Boolean(this.search || this.category);
  }

  isSubscribed(feedId: string): boolean {
    return this.subs.isSubscribed(feedId);
  }

  isSubscribePending(feedId: string): boolean {
    return this.subs.isPending(feedId);
  }

  /** Same not-signed-in redirect as FeedDetail.toggleSubscribe — see there. */
  quickSubscribe(feed: Feed): void {
    if (!this.auth.session()) {
      void this.router.navigate(['/login'], {
        queryParams: { redirect: '/', subscribeFeed: feed.id },
      });
      return;
    }
    this.subs.subscribe(feed.id, (err) => this.toast.danger(apiMessage(err, '訂閱失敗')));
  }
}
