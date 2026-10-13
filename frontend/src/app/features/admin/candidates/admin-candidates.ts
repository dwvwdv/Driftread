import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Observable } from 'rxjs';
import { AdminService } from '../../../services/admin';
import { FeedCandidate, SourceReview } from '../../../models';
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
  private destroyRef = inject(DestroyRef);
  private reviewSeq = new Map<string, number>();
  protected sourceReviews = signal<Record<string, SourceReview | null>>({});
  protected reviewErrors = signal<Set<string>>(new Set());
  protected reviewBusy = signal<Set<string>>(new Set());
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  private confirm = inject(ConfirmService);

  protected candidates = signal<FeedCandidate[]>([]);
  protected total = signal(0);
  protected status = signal<'pending' | 'held'>('pending');
  protected selected = signal<Set<string>>(new Set());
  protected reviewing = signal(false);
  protected groups = computed(() => {
    const groups = new Map<string, FeedCandidate[]>();
    for (const candidate of this.candidates()) {
      const domain = this.primaryDomain(candidate.source_host);
      groups.set(domain, [...(groups.get(domain) ?? []), candidate]);
    }
    return [...groups.entries()].map(([domain, items]) => ({ domain, items }));
  });
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

    this.admin
      .listCandidates(this.page(), this.pageSize(), this.status())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          if (seq !== this.loadSeq) return;
          this.candidates.set(result.items);
          this.selected.set(new Set());
          this.total.set(result.total);
          this.loading.set(false);
          for (const candidate of result.items) this.loadSourceReview(candidate.id);
        },
        error: () => {
          if (seq !== this.loadSeq) return;
          this.loading.set(false);
        },
      });
  }

  private nextReviewSeq(id: string): number {
    const seq = (this.reviewSeq.get(id) ?? 0) + 1;
    this.reviewSeq.set(id, seq);
    return seq;
  }

  private loadSourceReview(id: string): void {
    if (this.reviewBusy().has(id)) return;
    const seq = this.nextReviewSeq(id);
    this.admin
      .listSourceReviews(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ items }) => {
          if (seq !== this.reviewSeq.get(id)) return;
          this.sourceReviews.update((current) => ({ ...current, [id]: items[0] ?? null }));
          this.reviewErrors.update((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          });
        },
        error: () => {
          if (seq !== this.reviewSeq.get(id)) return;
          this.reviewErrors.update((current) => new Set(current).add(id));
        },
      });
  }

  protected requestReview(candidate: FeedCandidate, retry = false): void {
    if (this.reviewBusy().has(candidate.id) || this.reviewing()) return;
    const review = this.sourceReviews()[candidate.id];
    if (candidate.manual_override || review?.manual_override) {
      this.toast.warning('此候選已有人工決定，不能由模型覆寫');
      return;
    }
    if (
      !retry &&
      (review ||
        this.sourceReviews()[candidate.id] !== null ||
        this.reviewErrors().has(candidate.id))
    )
      return;
    if (retry && (!review || !this.canRetry(review))) return;
    const request = retry
      ? this.admin.retrySourceReview(review!.id)
      : this.admin.requestSourceReview(candidate.id);
    this.runReviewAction(candidate.id, request);
  }

  protected resolveReview(candidate: FeedCandidate, decision: 'accept' | 'reject' | 'defer'): void {
    const review = this.sourceReviews()[candidate.id];
    if (
      !review ||
      review.status !== 'awaiting_human' ||
      this.reviewBusy().has(candidate.id) ||
      this.reviewing() ||
      candidate.manual_override ||
      review.manual_override
    )
      return;
    this.runReviewAction(candidate.id, this.admin.resolveSourceReview(review.id, decision));
  }

  private runReviewAction(id: string, request: Observable<SourceReview>): void {
    this.nextReviewSeq(id);
    ++this.loadSeq;
    this.reviewBusy.update((current) => new Set(current).add(id));
    const finished = () =>
      this.reviewBusy.update((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (review) => {
        this.sourceReviews.update((current) => ({ ...current, [id]: review }));
        finished();
        this.toast.info('來源評審作業已更新');
        this.load(true);
      },
      error: () => {
        finished();
        this.load(true);
      },
    });
  }

  protected canRetry(review: SourceReview): boolean {
    return (
      ['rejected', 'deferred', 'failed', 'stale'].includes(review.status) && !review.manual_override
    );
  }

  protected reviewStatus(review: SourceReview): string {
    const labels: Record<SourceReview['status'], string> = {
      requested: '已排入評審',
      preparing: '準備樣本中',
      ready: '等待評審領取',
      leased: '評審中',
      awaiting_human: '等待人工覆核',
      accepted: '已收錄',
      rejected: '未通過',
      deferred: '暫緩',
      failed: '服務失敗',
      stale: '資料已變更',
      overridden: '人工決定優先',
    };
    return labels[review.status];
  }

  protected reviewProblem(review: SourceReview): string {
    if (!['failed', 'deferred', 'stale'].includes(review.status)) return '';
    const messages: Record<string, string> = {
      provider_key_missing: '尚未設定 Jev 金鑰，請由部署管理者設定後重試。',
      budget_exceeded: '模型預算已達上限，請等待額度恢復或調整預算後重試。',
      provider_outcome_unknown: '請求逾時或中斷，付費結果尚未確認；重試受後台次數限制。',
      request_unknown_paused: '付費請求結果持續不明，已暫停；請檢查請求紀錄後處理。',
      provider_disabled: '評審引擎目前未啟用，請檢查來源評審設定。',
      provider_model_unsupported: '模型版本尚未支援，請選擇目前支援的 Jev 版本。',
      provider_context_limit: '評審資料超出模型上限，請縮減樣本數或規準後重試。',
      sample_fetch_failed: '無法取得來源樣本，請檢查 feed 是否可讀後重試。',
      lease_exhausted: '評審領取後未完成，已達重試上限；請檢查評審服務後重試。',
      provider_http_error: '模型服務未成功回應，請檢查服務與金鑰狀態。',
      provider_invalid_response: '模型回應格式不符合評審規格，請檢查評審服務。',
      provider_response_too_large: '模型回應超出大小上限，請檢查評審服務。',
      request_in_progress: '同一模型請求仍在處理，請稍後重新整理。',
      request_fenced: '評審請求版本已變更，請重新整理後再試。',
      receipt_cache_expired: '已保存的模型結果已過期，請檢查請求紀錄後處理。',
      receipt_response_missing: '模型回應紀錄暫時無法取得，請檢查後台服務。',
    };
    if (review.error_code)
      return messages[review.error_code] ?? '評審服務未完成，請檢查服務狀態後重試。';
    if (review.status === 'stale') return '來源或評審設定已變更，請重新評審。';
    if (review.status === 'deferred') {
      if (review.result?.reason_codes.includes('low_confidence'))
        return '評審信心不足，請補充樣本或檢查收錄規準後重試。';
      return '樣本或評審結果未達收錄門檻，請檢查來源內容與評審設定後重試。';
    }
    return '評審服務未完成，請檢查服務狀態後重試。';
  }

  protected reviewDecision(review: SourceReview): string {
    const result = review.result;
    if (!result) return '';
    const label = { accept: '通過', reject: '不通過', abstain: '無法判定' }[result.decision];
    return `${label} · 信心 ${Math.round(result.confidence * 100)}% · 品質 ${Math.round(result.dimensions.quality * 100)}% · 垃圾機率 ${Math.round(result.dimensions.spam_probability * 100)}%`;
  }

  protected changeStatus(status: 'pending' | 'held'): void {
    if (status === this.status()) return;
    this.status.set(status);
    this.page.set(1);
    this.load();
  }

  protected toggle(id: string, checked: boolean): void {
    this.selected.update((current) => {
      const next = new Set(current);
      checked ? next.add(id) : next.delete(id);
      return next;
    });
  }

  protected toggleGroup(items: FeedCandidate[], checked: boolean): void {
    this.selected.update((current) => {
      const next = new Set(current);
      for (const item of items) checked ? next.add(item.id) : next.delete(item.id);
      return next;
    });
  }

  protected allSelected(items: FeedCandidate[]): boolean {
    return items.every((item) => this.selected().has(item.id));
  }

  protected async bulk(action: 'approve' | 'hold' | 'reject'): Promise<void> {
    const items = this.candidates().filter((candidate) => this.selected().has(candidate.id));
    if (!items.length || this.reviewing()) return;

    if (action === 'reject' && this.blockHostOnReject) {
      const ok = await this.confirm.ask({
        heading: `拒絕並封鎖 ${items.length} 個候選？`,
        body: '將封鎖所選候選的所有網域，之後不會再探測這些網域。',
        confirmLabel: '批量拒絕並封鎖',
        danger: true,
      });
      if (!ok || this.destroyRef.destroyed) return;
    }

    const tags = this.parsedTags();
    const requests: Observable<unknown>[] = items.map((candidate) =>
      action === 'approve'
        ? this.admin.approveCandidate(candidate.id, {
            category: this.category.trim() || null,
            tags,
          })
        : action === 'hold'
          ? this.admin.holdCandidate(candidate.id, { note: null })
          : this.admin.rejectCandidate(candidate.id, {
              note: null,
              block_host: this.blockHostOnReject,
            }),
    );

    this.reviewing.set(true);
    let finished = 0;
    let succeeded = 0;
    const completeOne = (ok: boolean) => {
      finished += 1;
      if (ok) succeeded += 1;
      if (finished !== requests.length) return;
      this.reviewing.set(false);
      this.toast.info(`批量作業完成：${succeeded}/${items.length}`);
      this.load();
    };
    for (const request of requests) {
      request
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe({ next: () => completeOne(true), error: () => completeOne(false) });
    }
  }

  private primaryDomain(host: string | null): string {
    if (!host) return '(未知網域)';
    const labels = host.toLowerCase().replace(/\.$/, '').split('.');
    if (labels.length <= 2) return labels.join('.');
    const compoundSuffixes = new Set([
      'co.uk',
      'org.uk',
      'com.au',
      'com.cn',
      'com.hk',
      'com.tw',
      'co.jp',
    ]);
    const suffix = labels.slice(-2).join('.');
    return labels.slice(compoundSuffixes.has(suffix) ? -3 : -2).join('.');
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
    const tags = this.parsedTags();

    this.admin
      .approveCandidate(candidate.id, { category: this.category.trim() || null, tags })
      .pipe(takeUntilDestroyed(this.destroyRef))
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

  protected hold(candidate: FeedCandidate): void {
    this.admin
      .holdCandidate(candidate.id, { note: null })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.toast.info('已保留為備用 RSS 節點');
          this.remove(candidate.id);
        },
        error: () => this.load(),
      });
  }

  private parsedTags(): string[] {
    return this.tags
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean);
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
      if (!ok || this.destroyRef.destroyed) return;
    }

    this.admin
      .rejectCandidate(candidate.id, { note: null, block_host: this.blockHostOnReject })
      .pipe(takeUntilDestroyed(this.destroyRef))
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
