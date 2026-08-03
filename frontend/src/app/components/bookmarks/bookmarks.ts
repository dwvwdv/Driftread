import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Article, BookmarkType } from '../../models';
import { apiMessage } from '../../shared/http-errors';
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
export class Bookmarks implements OnInit {
  protected auth = inject(AuthService);
  private me = inject(MeService);
  private toast = inject(ToastService);

  protected readonly tabs = ['收藏', '稍後閱讀'] as const;
  tabIndex = signal(0);
  items = signal<Article[]>([]);
  loading = signal(false);

  get tab(): BookmarkType {
    return this.tabIndex() === 0 ? 'favorite' : 'read_later';
  }

  get emptyMessage(): string {
    return this.tabIndex() === 0 ? '還沒有收藏任何文章。' : '稍後閱讀清單是空的。';
  }

  ngOnInit(): void {
    if (this.auth.session()) this.load();
  }

  onTab(index: number): void {
    this.tabIndex.set(index);
    this.load();
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
