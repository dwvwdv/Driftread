import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { Bookmarks } from './bookmarks';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { ToastService } from '../../ui/toast/toast';
import { Article, BookmarkType } from '../../models';

const article = (i: number) =>
  ({
    id: `article-${i}`,
    feed_id: 'feed-1',
    title: `文章 ${i}`,
    url: `https://example.com/${i}`,
    summary: null,
    author: null,
    published_at: null,
  }) as Article;

describe('Bookmarks tab scoping', () => {
  /** Held open so a delete can be left in flight across a tab switch. */
  let deletion: Subject<void>;
  let lists: Record<BookmarkType, Article[]>;

  function setup() {
    deletion = new Subject<void>();
    lists = {
      favorite: [article(1), article(2)],
      // The same article is in both lists — the backend allows that.
      read_later: [article(1), article(3)],
    };

    const me = {
      listBookmarks: (type: BookmarkType) => of(lists[type]),
      removeBookmark: () => deletion,
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Bookmarks],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session: () => ({ user: {} }) } },
        { provide: MeService, useValue: me },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Bookmarks);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  it('removes the row when the reader stays on the originating tab', () => {
    const page = setup();
    page.remove(article(1));
    deletion.next();

    expect(page.items().map((a) => a.id)).toEqual(['article-2']);
  });

  it('does not touch the other tab when the delete lands after a switch', () => {
    const page = setup();

    // Delete a favourite, then move to 稍後閱讀 before it resolves.
    page.remove(article(1));
    page.onTab(1);
    expect(page.items().map((a) => a.id)).toEqual(['article-1', 'article-3']);

    deletion.next();

    // article-1 is still a valid read-later entry; only the favourite went.
    expect(page.items().map((a) => a.id)).toEqual(['article-1', 'article-3']);
  });
});
