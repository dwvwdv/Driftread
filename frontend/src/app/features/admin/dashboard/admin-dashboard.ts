import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminService } from '../../../services/admin';
import { formatMiB, formatSignedMiB } from '../../../shared/bytes';
import {
  ArticleStorageStats,
  StorageHistory,
  OperationsStatus,
  DiscoveryStats,
  FeedHealthSummary,
} from '../../../models';
import { ObCard } from '../../../ui/card/card';
import { ObIcon } from '../../../ui/icon/icon';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObStat } from '../../../ui/stat/stat';
import { ToastService } from '../../../ui/toast/toast';
import { DiscoveryControlPanel } from '../discovery-control/discovery-control';
import { StorageInsights } from '../storage-insights/storage-insights';
import { ArticleCapControl } from '../article-cap/article-cap-control';

/**
 * Console overview: the ten discovery counters, the two on-demand pipeline
 * actions, and a health warning list.
 *
 * Everything loads in ngOnInit. The old admin page could not do that — it held the
 * key in a non-persisted signal, so any request fired before the operator typed it
 * was a guaranteed 403, and every panel needed a manual "load" button. With the
 * key in sessionStorage and the route behind a guard, a key is guaranteed present
 * by the time this renders.
 */
@Component({
  selector: 'app-admin-dashboard',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    ObCard,
    ObIcon,
    ObLoading,
    ObEmpty,
    ObPageHeader,
    ObStat,
    DiscoveryControlPanel,
    StorageInsights,
    ArticleCapControl,
  ],
  templateUrl: './admin-dashboard.html',
  styleUrl: './admin-dashboard.scss',
})
export class AdminDashboard implements OnInit {
  private admin = inject(AdminService);
  private toast = inject(ToastService);

  protected stats = signal<DiscoveryStats | null>(null);
  protected unhealthy = signal<FeedHealthSummary[]>([]);
  protected loading = signal(true);
  protected running = signal(false);
  protected discoveryAllowed = signal(false);
  protected refreshing = signal(false);
  protected operations = signal<OperationsStatus | null>(null);
  protected storage = signal<ArticleStorageStats | null>(null);
  protected operationsLoading = signal(true);
  protected storageLoading = signal(true);
  protected history = signal<StorageHistory | null>(null);
  protected historyLoading = signal(true);
  private snapshotRequest = 0;
  private insights = viewChild(StorageInsights);
  /** Kinds whose latest run stays visible even when refresh fills the recent list. */
  protected readonly latestKinds = ['retention', 'article_cap', 'storage'];

  protected bytes(value: number): string {
    return formatMiB(value);
  }

  protected dailyBytes(value: number): string {
    return formatSignedMiB(value);
  }

  protected runStatus(status: string): string {
    return (
      (
        {
          running: '執行中',
          completed: '完成',
          success: '完成',
          succeeded: '完成',
          cancelled: '已取消',
          failed: '失敗',
          partial: '部分失敗',
          interrupted: '中斷',
        } as Record<string, string>
      )[status] ?? status
    );
  }

  ngOnInit(): void {
    // The insights panel loads itself on init; only later refreshes reach it.
    this.load(false);
  }

  protected load(reloadInsights = true): void {
    const request = ++this.snapshotRequest;
    if (reloadInsights) this.insights()?.load();
    this.operationsLoading.set(true);
    this.storageLoading.set(true);
    this.historyLoading.set(true);
    this.admin.storageHistory().subscribe({
      next: (history) => {
        if (request !== this.snapshotRequest) return;
        this.history.set(history);
        this.historyLoading.set(false);
      },
      error: () => {
        if (request !== this.snapshotRequest) return;
        this.history.set(null);
        this.historyLoading.set(false);
      },
    });
    this.admin.operations().subscribe({
      next: (snapshot) => {
        if (request !== this.snapshotRequest) return;
        this.operations.set(snapshot);
        this.operationsLoading.set(false);
      },
      error: () => {
        if (request !== this.snapshotRequest) return;
        this.operations.set(null);
        this.operationsLoading.set(false);
      },
    });
    this.admin.storageStats().subscribe({
      next: (snapshot) => {
        if (request !== this.snapshotRequest) return;
        this.storage.set(snapshot);
        this.storageLoading.set(false);
      },
      error: () => {
        if (request !== this.snapshotRequest) return;
        this.storage.set(null);
        this.storageLoading.set(false);
      },
    });
    this.loading.set(true);
    this.admin.stats().subscribe({
      next: (stats) => {
        this.stats.set(stats);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });

    this.admin.listUnhealthy(50, 10).subscribe({
      next: (feeds) => this.unhealthy.set(feeds),
      // Reported by AdminService; an empty warning list is a survivable state for
      // this panel, so it does not block the page.
      error: () => this.unhealthy.set([]),
    });
  }

  protected runCycle(): void {
    if (!this.discoveryAllowed() || this.running()) return;
    this.running.set(true);
    this.admin.runCycle().subscribe({
      next: (summary) => {
        this.running.set(false);
        if (summary.paused) {
          this.toast.info('探索已暫停，待探索佇列已保留');
          this.load();
          return;
        }
        // Bracket access: DiscoveryCycleSummary's stage fields are
        // Record<string, number>, and noPropertyAccessFromIndexSignature rejects
        // dotted access on an index signature.
        const created = summary.harvest['targets_created'] ?? 0;
        const candidates = summary.probe['candidates_new'] ?? 0;
        this.toast.success(
          `完成：新增待探測 ${created}、新候選 ${candidates}、入庫 ${summary.imported}`,
        );
        this.load();
      },
      error: () => this.running.set(false),
    });
  }

  protected refreshDue(): void {
    this.refreshing.set(true);
    this.admin.refreshDue().subscribe({
      next: (summary) => {
        this.refreshing.set(false);
        this.toast.success(
          `處理 ${summary.processed}：更新 ${summary.updated}、未變更 ${summary.not_modified}、` +
            `新文章 ${summary.new_articles}、失敗 ${summary.failed}、封存 ${summary.archived}`,
        );
        this.load();
      },
      error: () => this.refreshing.set(false),
    });
  }
}
