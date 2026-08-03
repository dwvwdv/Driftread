import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminService } from '../../../services/admin';
import { FeedCandidate } from '../../../models';
import { ObCard } from '../../../ui/card/card';
import { ObIcon } from '../../../ui/icon/icon';
import { ObListRow } from '../../../ui/list-row/list-row';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObPaginator } from '../../../ui/paginator/paginator';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

/**
 * Review queue for feeds the platform discovered on its own.
 *
 * Approving writes only the source record; articles are fetched later by the
 * scheduler.
 */
@Component({
  selector: 'app-admin-candidates',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCard, ObIcon, ObListRow, ObLoading, ObEmpty, ObPageHeader, ObPaginator],
  templateUrl: './admin-candidates.html',
  styleUrl: './admin-candidates.scss',
})
export class AdminCandidates implements OnInit {
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  private confirm = inject(ConfirmService);

  protected candidates = signal<FeedCandidate[]>([]);
  protected total = signal(0);
  protected page = signal(1);
  protected pageSize = signal(20);
  protected loading = signal(true);

  /** Applied to whichever candidate is approved next. */
  protected category = '';
  protected tags = '';
  protected blockHostOnReject = false;

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.admin.listCandidates(this.page(), this.pageSize()).subscribe({
      next: (result) => {
        this.candidates.set(result.items);
        this.total.set(result.total);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  protected onPage(page: number): void {
    this.page.set(page);
    this.load();
  }

  protected onPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
    this.load();
  }

  protected approve(candidate: FeedCandidate): void {
    const tags = this.tags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);

    this.admin
      .approveCandidate(candidate.id, { category: this.category.trim() || null, tags })
      .subscribe({
        next: (feed) => {
          this.toast.success(`已入庫：${feed.title}`);
          this.remove(candidate.id);
        },
        // A 409 means it was already rejected elsewhere, so the row on screen is
        // stale. AdminService has explained it; reload so it disappears.
        error: () => this.load(),
      });
  }

  protected async reject(candidate: FeedCandidate): Promise<void> {
    if (this.blockHostOnReject) {
      const host = candidate.source_host ?? '該網域';
      const ok = await this.confirm.ask({
        heading: `一併封鎖 ${host}？`,
        body: `封鎖的是整個網域，不只是這一列。之後這個網域上的任何來源都不會再被提議，而且 API 沒有解除封鎖的端點。`,
        confirmLabel: '拒絕並封鎖',
        danger: true,
      });
      if (!ok) return;
    }

    this.admin
      .rejectCandidate(candidate.id, { note: null, block_host: this.blockHostOnReject })
      .subscribe({
        next: () => {
          this.toast.info(this.blockHostOnReject ? '已拒絕並封鎖該網域' : '已拒絕');
          this.remove(candidate.id);
        },
        error: () => this.load(),
      });
  }

  private remove(id: string): void {
    this.candidates.update((list) => list.filter((c) => c.id !== id));
    this.total.update((n) => Math.max(0, n - 1));
  }
}
