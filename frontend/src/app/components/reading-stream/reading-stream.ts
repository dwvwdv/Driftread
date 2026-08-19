import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../services/auth';
import { ReadingStreamService } from '../../services/reading-stream';
import { StreamArticle } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { stripHtml } from '../../shared/html';
import { ConfirmService } from '../../ui/confirm/confirm';
import { ObIcon } from '../../ui/icon/icon';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObLoading, ObError, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ObStat } from '../../ui/stat/stat';
import { ToastService } from '../../ui/toast/toast';

/**
 * The primary reading entry point (TODO.md "我的閱讀流"): an aggregated,
 * cursor-paginated timeline of every article from every feed the reader is
 * subscribed to, with unread counts, read/unread state and filters.
 *
 * "我的訂閱" (`my-feeds`) keeps its own role — source management (OPML,
 * unsubscribe) — this page is where reading actually happens; see the nav
 * link order in public-layout.
 */
@Component({
  selector: 'app-reading-stream',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObIcon, ObListRow, ObLoading, ObError, ObEmpty, ObPageHeader, ObStat],
  templateUrl: './reading-stream.html',
  styleUrl: './reading-stream.scss',
})
export class ReadingStream {
  protected auth = inject(AuthService);
  protected stream = inject(ReadingStreamService);
  private confirm = inject(ConfirmService);
  private toast = inject(ToastService);

  feedId = signal<string | null>(null);
  unreadOnly = signal(false);
  hideRead = signal(false);
  error = signal('');

  /** `stream.items()` narrowed by the client-only "隱藏已讀" toggle — distinct
   * from `unreadOnly`, which instead changes what GET /me/stream fetches in
   * the first place (see ReadingStreamService.load). */
  visibleItems = computed(() => {
    const items = this.stream.items();
    return this.hideRead() ? items.filter((a) => !a.is_read) : items;
  });

  rows = computed(() =>
    this.visibleItems().map((article) => ({ article, preview: stripHtml(article.summary) })),
  );

  get emptyMessage(): string {
    if (this.feedId() || this.unreadOnly() || this.hideRead()) {
      return '沒有符合目前篩選條件的文章。';
    }
    return '訂閱一些信息源，文章就會出現在這裡。';
  }

  /** User id the stream has already been loaded for — same asynchronous-
   * session-restore reasoning as MyFeeds/Bookmarks: a one-shot check in
   * ngOnInit would run before AuthService restores the persisted session on
   * a direct visit and never run again. */
  private loadedFor: string | null = null;

  constructor() {
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.reload();
    });
  }

  reload(): void {
    this.error.set('');
    this.stream.loadCounts((e) => this.toast.danger(apiMessage(e, '讀取未讀數失敗')));
    this.stream.load(
      { feedId: this.feedId(), unreadOnly: this.unreadOnly() },
      (e) => this.error.set(apiMessage(e, '讀取閱讀流失敗')),
    );
  }

  onUnreadOnly(checked: boolean): void {
    this.unreadOnly.set(checked);
    this.reload();
  }

  onFeedFilter(value: string): void {
    this.feedId.set(value || null);
    this.reload();
  }

  clearFilters(): void {
    this.feedId.set(null);
    this.unreadOnly.set(false);
    this.hideRead.set(false);
    this.reload();
  }

  loadMore(): void {
    this.stream.loadMore(
      { feedId: this.feedId(), unreadOnly: this.unreadOnly() },
      (e) => this.toast.danger(apiMessage(e, '載入更多失敗')),
    );
  }

  toggleRead(article: StreamArticle): void {
    if (article.is_read) {
      this.stream.markUnread(article.id, (e) => this.toast.danger(apiMessage(e, '標記未讀失敗')));
    } else {
      this.stream.markRead(article.id, (e) => this.toast.danger(apiMessage(e, '標記已讀失敗')));
    }
  }

  markPageRead(): void {
    this.stream.markAllReadInView(
      (marked) => {
        if (marked > 0) this.toast.info(`已標記 ${marked} 篇為已讀`);
      },
      (e) => this.toast.danger(apiMessage(e, '標記已讀失敗')),
    );
  }

  async markScopeRead(): Promise<void> {
    const scopedFeed = this.feedId();
    const scopedFeedTitle = scopedFeed
      ? (this.stream.feedCounts().find((f) => f.feed_id === scopedFeed)?.feed_title ?? '此來源')
      : null;
    const ok = await this.confirm.ask({
      heading: '全部標為已讀',
      body: scopedFeedTitle
        ? `將把「${scopedFeedTitle}」的所有文章標記為已讀，包含尚未載入的部分。`
        : '將把目前所有已訂閱來源的文章標記為已讀，包含尚未載入的部分。',
      confirmLabel: '全部標為已讀',
    });
    if (!ok) return;

    this.stream.markAllReadInScope(
      scopedFeed,
      (marked) => this.toast.info(`已標記 ${marked} 篇為已讀`),
      (e) => this.toast.danger(apiMessage(e, '標記已讀失敗')),
    );
  }
}
