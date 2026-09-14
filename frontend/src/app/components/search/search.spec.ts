import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { Search } from './search';
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

  function setup() {
    articleCalls = [];
    feedCalls = [];
    articleResponses = [];
    feedResponses = [];

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
        { provide: SearchService, useValue: search },
        { provide: FeedService, useValue: { getLanguages: () => of(['en', 'zh']) } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Search);
    fixture.detectChanges();
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
});
