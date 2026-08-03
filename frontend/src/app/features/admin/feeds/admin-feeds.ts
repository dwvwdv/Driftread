import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { AdminService } from '../../../services/admin';
import { Feed, FeedHealthSummary } from '../../../models';
import { ObIcon } from '../../../ui/icon/icon';
import { ObListRow } from '../../../ui/list-row/list-row';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObPaginator } from '../../../ui/paginator/paginator';
import { ObTabs } from '../../../ui/tabs/tabs';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { clampPage } from '../../../shared/paging';

/**
 * Feed inventory: active, archived, and unhealthy.
 *
 * The unhealthy tab is new to the UI — GET /admin/feeds/unhealthy has existed
 * since the health-tracking work but nothing ever called it, so health_score and
 * consecutive_failures were only visible in the database.
 */
@Component({
  selector: 'app-admin-feeds',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, ObIcon, ObListRow, ObLoading, ObEmpty, ObPageHeader, ObPaginator, ObTabs],
  templateUrl: './admin-feeds.html',
  styleUrl: './admin-feeds.scss',
})
export class AdminFeeds implements OnInit {
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  private confirm = inject(ConfirmService);

  protected readonly tabs = ['使用中', '已封存', '健康度偏低'] as const;
  protected tab = signal(0);

  protected active = signal<Feed[]>([]);
  protected activeTotal = signal(0);
  protected page = signal(1);
  protected pageSize = signal(50);

  protected archived = signal<Feed[]>([]);
  protected unhealthy = signal<FeedHealthSummary[]>([]);

  protected loading = signal(true);
  /** Feed ids with a refresh in flight, so each row can show its own state. */
  protected busy = signal<ReadonlySet<string>>(new Set());

  ngOnInit(): void {
    this.loadActive();
  }

  protected onTab(index: number): void {
    this.tab.set(index);
    if (index === 0) this.loadActive();
    if (index === 1) this.loadArchived();
    if (index === 2) this.loadUnhealthy();
  }

  protected reload(): void {
    this.onTab(this.tab());
  }

  protected loadActive(): void {
    this.loading.set(true);
    this.admin.listFeeds(this.page(), this.pageSize(), false).subscribe({
      next: (result) => {
        this.active.set(result.items);
        this.activeTotal.set(result.total);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  protected loadArchived(): void {
    this.loading.set(true);
    this.admin.listArchived().subscribe({
      next: (feeds) => {
        this.archived.set(feeds);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  protected loadUnhealthy(): void {
    this.loading.set(true);
    this.admin.listUnhealthy().subscribe({
      next: (feeds) => {
        this.unhealthy.set(feeds);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  protected onPage(page: number): void {
    this.page.set(page);
    this.loadActive();
  }

  protected onPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
    this.loadActive();
  }

  protected isBusy(id: string): boolean {
    return this.busy().has(id);
  }

  protected async archive(feed: Feed): Promise<void> {
    const ok = await this.confirm.ask({
      heading: `封存「${feed.title}」？`,
      body: '封存後這個來源不再出現在前台，排程器也會停止抓取。可以隨時從「已封存」分頁恢復。',
      confirmLabel: '封存',
    });
    if (!ok) return;

    this.admin.archive(feed.id).subscribe({
      next: () => {
        this.toast.success(`已封存：${feed.title}`);
        this.active.update((list) => list.filter((f) => f.id !== feed.id));
        this.activeTotal.update((n) => Math.max(0, n - 1));

        // Archiving the last row on a page must not read as "no active feeds":
        // the empty branch hides the paginator with it, so the remaining feeds
        // would be unreachable without a manual reload.
        if (this.active().length === 0 && this.activeTotal() > 0) {
          this.page.set(clampPage(this.page(), this.activeTotal(), this.pageSize()));
          this.loadActive();
        }
      },
      error: () => undefined,
    });
  }

  protected unarchive(feed: Feed): void {
    this.admin.unarchive(feed.id).subscribe({
      next: () => {
        this.toast.success(`已恢復：${feed.title}`);
        this.archived.update((list) => list.filter((f) => f.id !== feed.id));
      },
      error: () => undefined,
    });
  }

  protected refresh(id: string, title: string): void {
    this.setBusy(id, true);
    this.admin.refreshFeed(id).subscribe({
      next: (result) => {
        this.setBusy(id, false);
        this.toast.success(`${title}：新增 ${result.inserted} 篇`);
      },
      // A 502 here means the remote feed did not answer — reported by
      // AdminService as a warning, and it must not stop the other rows.
      error: () => this.setBusy(id, false),
    });
  }

  private setBusy(id: string, busy: boolean): void {
    this.busy.update((set) => {
      const next = new Set(set);
      if (busy) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }
}
