import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { Subject, of } from 'rxjs';
import { ArticleReader } from './article-reader';
import { ArticleService } from '../../services/article';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { Article, BookmarkType } from '../../models';

const ARTICLE = {
  id: 'article-1',
  feed_id: 'feed-1',
  title: '測試文章',
  url: 'https://example.com/post',
  summary: null,
  author: null,
  published_at: null,
  content: '<p>內容</p>',
  fetched_at: '2026-08-01T00:00:00Z',
} as Article;

describe('ArticleReader session restore race', () => {
  it('loads bookmark state when the session arrives after the article', async () => {
    // AuthService restores the persisted session asynchronously and the article
    // is a public request, so the article routinely wins. Sampling session()
    // once in the article callback missed it and never retried.
    const session = signal<{ user: { id: string } } | null>(null);
    const favorites = new Subject<Article[]>();
    const marked: string[] = [];

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ArticleReader],
      providers: [
        { provide: ArticleService, useValue: { getArticle: () => of(ARTICLE) } },
        { provide: AuthService, useValue: { session } },
        {
          provide: MeService,
          useValue: {
            markRead: (id: string) => {
              marked.push(id);
              return of(undefined);
            },
            listBookmarks: (type: BookmarkType) =>
              type === 'favorite' ? favorites : of([] as Article[]),
            addBookmark: () => of(undefined),
            removeBookmark: () => of(undefined),
          },
        },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'article-1' }) } },
        },
      ],
    });

    const fixture = TestBed.createComponent(ArticleReader);
    fixture.detectChanges();

    // Article has landed; session has not. Nothing signed-in should have run.
    expect(marked).toEqual([]);

    session.set({ user: { id: 'user-1' } });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(marked).toEqual(['article-1']);

    favorites.next([ARTICLE]);
    expect(fixture.componentInstance.favorited()).toBe(true);
  });
});

describe('ArticleReader bookmark state', () => {
  /** Membership reads are held open so the race window can be driven by hand. */
  let favoriteRead: Subject<Article[]>;
  let readLaterRead: Subject<Article[]>;
  let added: BookmarkType[];

  function setup() {
    favoriteRead = new Subject<Article[]>();
    readLaterRead = new Subject<Article[]>();
    added = [];

    const me = {
      markRead: () => of(undefined),
      listBookmarks: (type: BookmarkType) => (type === 'favorite' ? favoriteRead : readLaterRead),
      addBookmark: (_id: string, type: BookmarkType) => {
        added.push(type);
        return of(undefined);
      },
      removeBookmark: () => of(undefined),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ArticleReader],
      providers: [
        { provide: ArticleService, useValue: { getArticle: () => of(ARTICLE) } },
        { provide: AuthService, useValue: { session: () => ({ user: {} }) } },
        { provide: MeService, useValue: me },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'article-1' }) } },
        },
      ],
    });

    const fixture = TestBed.createComponent(ArticleReader);
    fixture.detectChanges();
    return fixture;
  }

  it('applies the membership read when the reader has not touched anything', () => {
    const fixture = setup();

    favoriteRead.next([ARTICLE]);
    readLaterRead.next([]);

    expect(fixture.componentInstance.favorited()).toBe(true);
    expect(fixture.componentInstance.readLater()).toBe(false);
  });

  it('does not let a stale read undo a bookmark added while it was in flight', () => {
    const fixture = setup();
    const reader = fixture.componentInstance;

    // The buttons are live before the reads land: the user favourites now.
    reader.toggle('favorite');
    expect(added).toEqual(['favorite']);
    expect(reader.favorited()).toBe(true);

    // The read that started *before* the click now resolves, still reporting the
    // old state. Without the guard this reset the star to empty and left the UI
    // disagreeing with the server until a reload.
    favoriteRead.next([]);

    expect(reader.favorited()).toBe(true);
  });

  it('still applies reads for the type that was not touched', () => {
    const fixture = setup();
    const reader = fixture.componentInstance;

    reader.toggle('favorite');
    favoriteRead.next([]);
    readLaterRead.next([ARTICLE]);

    expect(reader.favorited()).toBe(true);
    expect(reader.readLater()).toBe(true);
  });
});
