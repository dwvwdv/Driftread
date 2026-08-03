import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AdminService } from '../../../services/admin';
import { DiscoveryStats, FeedHealthSummary } from '../../../models';
import { ObCard } from '../../../ui/card/card';
import { ObIcon } from '../../../ui/icon/icon';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObStat } from '../../../ui/stat/stat';
import { ToastService } from '../../../ui/toast/toast';

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
  imports: [RouterLink, ObCard, ObIcon, ObLoading, ObEmpty, ObPageHeader, ObStat],
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
  protected refreshing = signal(false);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
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
    this.running.set(true);
    this.admin.runCycle().subscribe({
      next: (summary) => {
        this.running.set(false);
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
