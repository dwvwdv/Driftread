import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Article, BookmarkType } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { stripHtml } from '../../shared/html';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObLoading, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ObTabs } from '../../ui/tabs/tabs';
import { ToastService } from '../../ui/toast/toast';

/**
 * Saved articles: favourites and read-later.
 *
 * Rendered as dense rows rather than a card grid — a reading list is scanned, not
 * browsed.
 */
@Component({
  selector: 'app-bookmarks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DatePipe, ObListRow, ObLoading, ObEmpty, ObPageHeader, ObTabs],
  templateUrl: './bookmarks.html',
  styleUrl: './bookmarks.scss',
})
export class Bookmarks {
  protected auth = inject(AuthService);
  private me = inject(MeService);
  private toast = inject(ToastService);

  protected readonly tabs = ['收藏', '稍後閱讀'] as const;
  tabIndex = signal(0);
  items = signal<Article[]>([]);
  loading = signal(false);

  /**
   * Rows with the preview text derived once per list, rather than calling
   * stripHtml() from the template on every change-detection pass — a summary
   * written before the parser fix can hold a whole article's worth of markup.
   */
  rows = computed(() =>
    this.items().map((article) => ({ article, preview: stripHtml(article.summary) })),
  );

  get tab(): BookmarkType {
    return this.tabIndex() === 0 ? 'favorite' : 'read_later';
  }

  get emptyMessage(): string {
    return this.tabIndex() === 0 ? '還沒有收藏任何文章。' : '稍後閱讀清單是空的。';
  }

  /** User id the current tab has already been loaded for. */
  private loadedFor: string | null = null;

  constructor() {
    // Same reason as my-feeds: AuthService restores the persisted session
    // asynchronously, so a one-shot `if (session())` in ngOnInit runs too early
    // on a direct visit and never runs again — the tabs would render with an
    // empty list and claim there were no bookmarks.
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.load();
    });
  }

  onTab(index: number): void {
    this.tabIndex.set(index);
    // Tab changes load directly: the effect is keyed on identity, not on the tab.
    if (this.auth.session()) this.load();
  }

  load(): void {
    this.loading.set(true);
    this.me.listBookmarks(this.tab).subscribe({
      next: (articles) => {
        this.items.set(articles);
        this.loading.set(false);
      },
      error: (e: unknown) => {
        this.loading.set(false);
        this.toast.danger(apiMessage(e, '讀取失敗'));
      },
    });
  }

  remove(article: Article): void {
    // Captured up front. An article may be both a favourite and read-later, so if
    // the reader switches tabs while the delete is in flight, filtering whatever
    // list is on screen when it lands would hide a still-valid entry from the
    // other tab. The delete itself is already scoped to `from`; the local update
    // has to be too.
    const from = this.tab;

    this.me.removeBookmark(article.id, from).subscribe({
      next: () => {
        this.toast.info('已移除');
        // Moved on: the visible list was never the one this touched, and it was
        // reloaded on the tab switch anyway.
        if (this.tab !== from) return;
        this.items.update((list) => list.filter((a) => a.id !== article.id));
      },
      error: (e: unknown) => this.toast.danger(apiMessage(e, '移除失敗')),
    });
  }
}
