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
import { clampPage } from '../../../shared/paging';

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

  /**
   * Generation counter for list requests.
   *
   * Reviewing a row triggers a backfill, so several loads can be in flight after
   * a quick run of approvals. They are not interchangeable: an earlier one
   * observed the queue before later approvals landed, so applying it out of order
   * resurrects rows that are already gone. Only the newest response is applied.
   */
  private loadSeq = 0;

  /**
   * @param quiet keeps the rendered rows up while refetching. A backfill after a
   * review must not swap the list for a spinner — that is a full teardown between
   * every two decisions, and the row the operator is reading vanishes underneath
   * them. Navigation and first load still show the spinner, because there is
   * genuinely nothing valid to keep on screen.
   */
  protected load(quiet = false): void {
    const seq = ++this.loadSeq;
    if (!quiet) this.loading.set(true);

    this.admin.listCandidates(this.page(), this.pageSize()).subscribe({
      next: (result) => {
        if (seq !== this.loadSeq) return;
        this.candidates.set(result.items);
        this.total.set(result.total);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.loadSeq) return;
        this.loading.set(false);
      },
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

  /**
   * Drops a reviewed row and refills the page from the server.
   *
   * The local removal is what makes the click feel instant, but it cannot be the
   * whole story. The backend paginates with `.range(offset, …)` over rows filtered
   * to `status = 'pending'`, and reviewing a row takes it out of that filter — so
   * every candidate after it shifts down one index. Drop a row from page 1 without
   * refetching and the first candidate of page 2 has silently moved onto page 1,
   * which we are no longer showing; press Next and it is skipped. One row per
   * review, never seen, with nothing on screen to suggest anything was missed.
   * That is a bad failure for a queue whose entire job is "look at every row".
   *
   * A page-based API cannot express "resume from offset 19", so compensating at
   * navigation time is not available: the only way to stay aligned is to refetch
   * while still on the page. Quietly, so the list does not blink between decisions.
   *
   * Clamped first because reviewing shifts the boundaries: the current index can
   * end up past the end, and the empty branch hides the paginator with it.
   */
  private remove(id: string): void {
    this.candidates.update((list) => list.filter((c) => c.id !== id));
    this.total.update((n) => Math.max(0, n - 1));

    if (this.total() === 0) return;

    this.page.set(clampPage(this.page(), this.total(), this.pageSize()));
    this.load(this.candidates().length > 0);
  }
}
