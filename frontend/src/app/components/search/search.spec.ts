import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { Search } from './search';
import { AuthService } from '../../services/auth';
import { FeedService } from '../../services/feed';
import { SearchService } from '../../services/search';
import { ToastService } from '../../ui/toast/toast';
import {
  ArticleSearchResult,
  FeedSearchResult,
  PaginatedArticleSearchResults,
  PaginatedFeedSearchResults,
} from '../../models';

const articleResult = (i: number, overrides: Partial<ArticleSearchResult> = {}): ArticleSearchResult => ({
  id: `article-${i}`,
  feed_id: 'feed-1',
  feed_title: 'Some Feed',
  title: `文章 ${i}`,
  url: `https://example.com/${i}`,
  summary: null,
  snippet: null,
  author: null,
  published_at: null,
  fetched_at: '2026-08-14T10:00:00Z',
  is_read: false,
  is_bookmarked: false,
  rank: 0.5,
  ...overrides,
});

const feedResult = (i: number): FeedSearchResult => ({
  id: `feed-${i}`,
  title: `Feed ${i}`,
  url: `https://example.com/feed-${i}.xml`,
  description: null,
  snippet: null,
  website_url: null,
  language: 'en',
  category: null,
  tags: [],
  article_count: 0,
  created_at: '2026-08-14T10:00:00Z',
  rank: 0.5,
});

function resultPage<T>(items: T[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor };
}

describe('Search', () => {
  let articleCalls: { q: string; language: string | null | undefined; cursor: string | null | undefined }[];
  let feedCalls: { q: string; language: string | null | undefined; cursor: string | null | undefined }[];
  let articleResponses: Subject<PaginatedArticleSearchResults>[];
  let feedResponses: Subject<PaginatedFeedSearchResults>[];
  let session: ReturnType<typeof signal<{ user: { id: string } } | null>>;
  // Re-detects changes on the same fixture, which is how a component-owned
  // effect (created via `effect()` in Search's constructor) actually flushes
  // in tests — same reasoning as my-feeds.spec.ts's own `detect`.
  let detect: () => void;

  function setup() {
    articleCalls = [];
    feedCalls = [];
    articleResponses = [];
    feedResponses = [];
    session = signal<{ user: { id: string } } | null>(null);

    const search = {
      searchArticles: (q: string, language?: string | null, cursor?: string | null) => {
        articleCalls.push({ q, language, cursor });
        const subject = new Subject<PaginatedArticleSearchResults>();
        articleResponses.push(subject);
        return subject;
      },
      searchFeeds: (q: string, language?: string | null, cursor?: string | null) => {
        feedCalls.push({ q, language, cursor });
        const subject = new Subject<PaginatedFeedSearchResults>();
        feedResponses.push(subject);
        return subject;
      },
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Search],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session } },
        { provide: SearchService, useValue: search },
        { provide: FeedService, useValue: { getLanguages: () => of(['en', 'zh']) } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Search);
    detect = () => fixture.detectChanges();
    detect();
    return fixture.componentInstance;
  }

  it('does nothing on submit with an empty (or whitespace-only) query', () => {
    const page = setup();
    page.query = '   ';
    page.submit();
    expect(articleCalls.length).toBe(0);
    expect(page.hasQuery).toBe(false);
  });

  it('searches articles on submit and populates results', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();

    expect(articleCalls).toEqual([{ q: 'rust', language: null, cursor: null }]);
    articleResponses[0].next(resultPage([articleResult(1), articleResult(2)]));

    expect(p.articleItems().map((a) => a.id)).toEqual(['article-1', 'article-2']);
    expect(p.articleLoading()).toBe(false);
  });

  it('does not re-fetch the feeds tab a second time for the same query and filter', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    articleResponses[0].next(resultPage([]));

    p.onTab(1);
    feedResponses[0].next(resultPage([feedResult(1)]));
    expect(feedCalls.length).toBe(1);

    // Switching back to articles then to feeds again should not re-issue the
    // feeds request — same query, same language filter.
    p.onTab(0);
    p.onTab(1);
    expect(feedCalls.length).toBe(1);
    expect(p.feedItems().map((f) => f.id)).toEqual(['feed-1']);
  });

  it('re-fetches the active tab when the language filter changes', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    articleResponses[0].next(resultPage([]));

    p.onLanguage('en');

    expect(articleCalls).toEqual([
      { q: 'rust', language: null, cursor: null },
      { q: 'rust', language: 'en', cursor: null },
    ]);
  });

  it('loadMoreArticles appends the next page and advances the cursor', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    articleResponses[0].next(resultPage([articleResult(1)], 'cursor-1'));

    p.loadMoreArticles();
    expect(articleCalls[1]).toEqual({ q: 'rust', language: null, cursor: 'cursor-1' });
    articleResponses[1].next(resultPage([articleResult(2)], null));

    expect(p.articleItems().map((a) => a.id)).toEqual(['article-1', 'article-2']);
    expect(p.hasMoreArticles()).toBe(false);
  });

  it('drops a stale in-flight article response once a fresh submit supersedes it', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    const stale = articleResponses[0];

    p.query = 'python';
    p.submit();
    stale.next(resultPage([articleResult(99)]));

    // The stale 'rust' response must not land after 'python' superseded it.
    expect(p.articleItems()).toEqual([]);

    articleResponses[1].next(resultPage([articleResult(1)]));
    expect(p.articleItems().map((a) => a.id)).toEqual(['article-1']);
  });

  it('surfaces a search error without leaving the loading state stuck', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    articleResponses[0].error({ status: 500 });

    expect(p.articleLoading()).toBe(false);
    expect(p.articleError()).toBeTruthy();
  });

  it('reloads article results once a persisted session resolves after an anonymous search', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    expect(articleCalls.length).toBe(1);
    articleResponses[0].next(resultPage([articleResult(1, { is_read: false })]));
    expect(p.articleItems()[0].is_read).toBe(false);

    session.set({ user: { id: 'user-1' } });
    detect();

    expect(articleCalls.length).toBe(2);
    articleResponses[1].next(resultPage([articleResult(1, { is_read: true })]));
    expect(p.articleItems()[0].is_read).toBe(true);
  });

  it('does not reload when there is no active query yet', () => {
    // A session resolving before any search is submitted must not fire a
    // request — hasQuery is false, so the effect's own guard should no-op.
    setup();
    session.set({ user: { id: 'user-1' } });
    detect();

    expect(articleCalls.length).toBe(0);
  });

  it('invalidates (but does not immediately reload) the feed tab on identity change, reloading on switch back', () => {
    const p = setup();
    p.query = 'rust';
    p.submit();
    articleResponses[0].next(resultPage([]));

    p.onTab(1);
    feedResponses[0].next(resultPage([feedResult(1)]));
    expect(feedCalls.length).toBe(1);

    session.set({ user: { id: 'user-1' } });
    detect();
    // Identity change while the feed tab is active must not fire an article
    // request (feed results don't carry per-user state).
    expect(articleCalls.length).toBe(1);

    p.onTab(0);
    // Switching back to the (now identity-invalidated) article tab reloads.
    expect(articleCalls.length).toBe(2);
  });
});
