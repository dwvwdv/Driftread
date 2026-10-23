import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
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
  imports: [DatePipe, FormsModule, ObIcon, ObListRow, ObLoading, ObEmpty, ObPageHeader, ObPaginator, ObTabs],
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
  protected editing = signal<Feed | null>(null);
  protected savingSource = signal(false);
  protected sourceRole: 'normal' | 'signal_only' | 'private' = 'normal';
  protected fulltextPolicy: 'rss' | 'summary_only' = 'rss';
  protected signalGroup = '';
  protected firstParty = false;
  /** Empty means "use the global article.cap setting". */
  protected articleCap: number | null = null;

  protected editSource(feed: Feed): void {
    this.editing.set(feed);
    this.sourceRole = feed.participation_mode ?? 'normal';
    this.fulltextPolicy = feed.fulltext_policy ?? 'rss';
    this.signalGroup = feed.signal_group ?? '';
    this.firstParty = feed.first_party ?? false;
    this.articleCap = feed.article_cap ?? null;
  }

  protected async saveSource(): Promise<void> {
    const feed = this.editing();
    if (!feed || this.savingSource()) return;
    // Angular's number accessor maps an emptied input to null.
    const cap = this.articleCap ?? null;
    if (cap !== null && (!Number.isInteger(cap) || cap < 100 || cap > 100000)) {
      this.toast.warning('文章上限須為 100–100000 的整數，或留空使用全域設定');
      return;
    }
    // Same irreversible deletion as lowering the global cap: confirm a new or
    // lower override. The global value is not known here, so any new one asks.
    const previous = feed.article_cap ?? null;
    if (cap !== null && (previous === null || cap < previous)) {
      const ok = await this.confirm.ask({
        heading: `「${feed.title}」只保留 ${cap} 篇？`,
        body: '排程會逐批刪除這個來源超過上限的舊文章與其已讀紀錄，刪除後無法還原。收藏、稍後讀與人工事件引用的文章不受影響。',
        confirmLabel: '儲存',
      });
      if (!ok || this.editing() !== feed || this.savingSource()) return;
    }
    this.savingSource.set(true);
    this.admin.updateSourceMetadata(feed.id, {
      participation_mode: this.sourceRole,
      fulltext_policy: this.fulltextPolicy,
      signal_group: this.signalGroup.trim() || null,
      first_party: this.firstParty,
      article_cap: cap,
    }).subscribe({
      next: () => {
        this.savingSource.set(false);
        this.editing.set(null);
        this.toast.success('來源設定已儲存');
        this.reload();
      },
      error: () => this.savingSource.set(false),
    });
  }

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

  /**
   * Generation counter for the active list — see the note on archive(). Several
   * backfills can overlap during a run of archives, and an earlier response
   * describes a list that later archives have already changed.
   */
  private activeSeq = 0;

  /** @param quiet keeps the current rows up while refetching; see archive(). */
  protected loadActive(quiet = false): void {
    const seq = ++this.activeSeq;
    if (!quiet) this.loading.set(true);

    this.admin.listFeeds(this.page(), this.pageSize(), false).subscribe({
      next: (result) => {
        if (seq !== this.activeSeq) return;
        this.active.set(result.items);
        this.activeTotal.set(result.total);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.activeSeq) return;
        this.loading.set(false);
      },
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

  protected fetchStatus(feed: Feed): string {
    if (!feed.admitted_at && feed.last_failure_reason?.startsWith('Excluded before admission:')) {
      return feed.last_failure_reason.includes('comment feed')
        ? '未准入：留言來源' : '未准入：重複來源';
    }
    const result = feed.last_fetch_result;
    if (feed.admitted_at && !feed.archived_at && result?.outcome === 'excluded') {
      return '已人工核准，等待重新抓取';
    }
    const labels: Record<string, string> = {
      in_progress: '抓取已開始，尚未記錄完成結果',
      fetch_failed: '抓取失敗', fetch_timeout: '抓取逾時',
      http_error: '來源 HTTP 錯誤', parse_failed: 'RSS／Atom 解析失敗',
      ingest_failed: '文章寫入或統計失敗', excluded: '未通過來源准入檢查',
      empty_feed: '來源回傳零篇文章', no_usable_entries: '所有項目都缺少文章網址',
      not_modified: '來源回覆未變更（304）', unchanged: '沒有新增文章', updated: '文章已更新',
    };
    if (result) return labels[result.outcome] ?? '抓取結果未知';
    if (feed.archived_at) return '已封存；歷史抓取結果未記錄';
    if (!feed.last_fetched_at) return feed.last_fetch_at ? '已嘗試抓取，尚無完成結果' : '等待首次抓取';
    return feed.article_count === 0 ? '尚無文章；歷史原因未記錄' : '歷史抓取結果未記錄';
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

        // Refetch, not just a local splice. GET /admin/feeds pages with
        // `.range(offset, …)` over rows filtered by `archived`, so archiving one
        // feed shifts every later feed down an index. Leave the short page up and
        // the first feed of page 2 has already moved onto page 1 server-side —
        // pressing Next then steps straight over it, once per archive, silently.
        //
        // Quiet while rows remain so the list does not blink between actions; loud
        // when the page emptied, since there is nothing valid left to keep up and
        // the empty branch would otherwise hide the paginator along with it.
        if (this.activeTotal() === 0) return;

        this.page.set(clampPage(this.page(), this.activeTotal(), this.pageSize()));
        this.loadActive(this.active().length > 0);
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
        // new_articles, not inserted. `inserted` is rows touched by the upsert —
        // for an unchanged feed that is its whole item list, so reporting it told
        // the operator dozens of articles arrived when nothing had. The backend
        // returns both and documents the distinction at
        // backend/routers/admin.py::refresh_feed.
        this.toast.success(`${title}：新增 ${result.new_articles} 篇`);
        this.reload();
      },
      // A 502 here means the remote feed did not answer — reported by
      // AdminService as a warning, and it must not stop the other rows.
      error: () => { this.setBusy(id, false); this.reload(); },
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
