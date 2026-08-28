import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { FeedDetail } from './feed-detail';
import { ArticleService } from '../../services/article';
import { FeedService } from '../../services/feed';
import { MeService } from '../../services/me';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { FeedArticle, FeedWithArticles, PaginatedFeedArticles } from '../../models';

const feed: FeedWithArticles = {
  id: 'feed-1',
  title: 'Feed One',
  url: 'https://example.com/feed.xml',
  website_url: null,
  description: null,
  category: null,
  language: null,
  tags: [],
  article_count: 0,
  last_fetched_at: null,
  archived_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  articles: [],
};

const noArticles: PaginatedFeedArticles = { items: [], next_cursor: null };

function makeArticle(overrides: Partial<FeedArticle> = {}): FeedArticle {
  return {
    id: 'article-1',
    feed_id: 'feed-1',
    title: 'Article One',
    url: 'https://example.com/a',
    summary: null,
    author: null,
    published_at: '2026-01-01T00:00:00Z',
    fetched_at: '2026-01-01T00:00:00Z',
    is_read: false,
    is_bookmarked: false,
    ...overrides,
  };
}

describe('FeedDetail subscribe action', () => {
  // Signed in by default; a test that needs signed-out sets this to null
  // *before* calling setup() — setup() itself must not touch it, or it would
  // clobber that override right back to signed-in.
  let session: { user: { id: string } } | null = { user: { id: 'user-1' } };
  let subs: {
    subscribed: Set<string>;
    subscribeCalls: string[];
    unsubscribeCalls: string[];
    isSubscribed: (id: string) => boolean;
    isPending: (id: string) => boolean;
    subscribe: (id: string) => void;
    unsubscribe: (id: string) => void;
  };
  let navCalls: unknown[][];

  beforeEach(() => {
    session = { user: { id: 'user-1' } };
  });

  function setup() {
    subs = {
      subscribed: new Set<string>(),
      subscribeCalls: [],
      unsubscribeCalls: [],
      isSubscribed: (id) => subs.subscribed.has(id),
      isPending: () => false,
      subscribe: (id) => subs.subscribeCalls.push(id),
      unsubscribe: (id) => subs.unsubscribeCalls.push(id),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [FeedDetail],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'feed-1' }) } },
        },
        { provide: FeedService, useValue: { getFeed: () => of(feed) } },
        { provide: ArticleService, useValue: { getArticles: () => of(noArticles) } },
        { provide: MeService, useValue: {} },
        { provide: RecommendationService, useValue: { liked: () => [], disliked: () => [] } },
        { provide: SubscriptionService, useValue: subs },
        { provide: AuthService, useValue: { session: () => session } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(FeedDetail);
    fixture.detectChanges();

    navCalls = [];
    const router = TestBed.inject(Router);
    router.navigate = (commands: any, extras?: any) => {
      navCalls.push([commands, extras]);
      return Promise.resolve(true);
    };

    return fixture.componentInstance;
  }

  it('sends a signed-out reader to log in with the feed to subscribe on return', () => {
    session = null;
    const page = setup();

    page.toggleSubscribe();

    expect(navCalls).toEqual([
      [['/login'], { queryParams: { redirect: '/feeds/feed-1', subscribeFeed: 'feed-1' } }],
    ]);
    expect(subs.subscribeCalls).toEqual([]);
  });

  it('subscribes a signed-in reader directly, with no navigation', () => {
    const page = setup();

    page.toggleSubscribe();

    expect(subs.subscribeCalls).toEqual(['feed-1']);
    expect(navCalls).toEqual([]);
  });

  it('unsubscribes when already subscribed', () => {
    const page = setup();
    subs.subscribed.add('feed-1');

    page.toggleSubscribe();

    expect(subs.unsubscribeCalls).toEqual(['feed-1']);
    expect(subs.subscribeCalls).toEqual([]);
  });
});

describe('FeedDetail article list', () => {
  let articleService: {
    calls: (string | null)[];
    getArticles: (feedId: string, cursor?: string | null) => ReturnType<typeof of<PaginatedFeedArticles>>;
  };
  let me: {
    markRead: (id: string) => ReturnType<typeof of<void>>;
    markUnread: (id: string) => ReturnType<typeof of<void>>;
    addBookmark: (id: string, type: string) => ReturnType<typeof of<void>>;
    removeBookmark: (id: string, type: string) => ReturnType<typeof of<void>>;
  };
  let firstPage: PaginatedFeedArticles;
  let secondPage: PaginatedFeedArticles;

  function setup() {
    firstPage = { items: [makeArticle()], next_cursor: 'cursor-1' };
    secondPage = { items: [makeArticle({ id: 'article-2' })], next_cursor: null };
    articleService = {
      calls: [],
      getArticles: (_feedId: string, cursor: string | null = null) => {
        articleService.calls.push(cursor);
        return of(cursor ? secondPage : firstPage);
      },
    };
    me = {
      markRead: () => of(undefined),
      markUnread: () => of(undefined),
      addBookmark: () => of(undefined),
      removeBookmark: () => of(undefined),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [FeedDetail],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { paramMap: convertToParamMap({ id: 'feed-1' }) } },
        },
        { provide: FeedService, useValue: { getFeed: () => of(feed) } },
        { provide: ArticleService, useValue: articleService },
        { provide: MeService, useValue: me },
        { provide: RecommendationService, useValue: { liked: () => [], disliked: () => [] } },
        {
          provide: SubscriptionService,
          useValue: { isSubscribed: () => false, isPending: () => false },
        },
        { provide: AuthService, useValue: { session: () => ({ user: { id: 'user-1' } }) } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(FeedDetail);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  it('loads the first page of articles on init', () => {
    const page = setup();

    expect(page.articles().map((a) => a.id)).toEqual(['article-1']);
    expect(page.hasMoreArticles()).toBe(true);
    expect(articleService.calls).toEqual([null]);
  });

  it('appends the next page and clears hasMoreArticles once exhausted', () => {
    const page = setup();

    page.loadMoreArticles();

    expect(page.articles().map((a) => a.id)).toEqual(['article-1', 'article-2']);
    expect(page.hasMoreArticles()).toBe(false);
    expect(articleService.calls).toEqual([null, 'cursor-1']);
  });

  it('optimistically marks an article read, then confirms on success', () => {
    const page = setup();
    let seenId = '';
    me.markRead = (id: string) => {
      seenId = id;
      return of(undefined);
    };

    page.toggleRead(page.articles()[0]);

    expect(page.articles()[0].is_read).toBe(true);
    expect(seenId).toBe('article-1');
    expect(page.isReadPending('article-1')).toBe(false);
  });

  it('rolls back the optimistic is_read flip on failure', () => {
    const page = setup();
    me.markRead = () => throwError(() => new Error('boom'));

    page.toggleRead(page.articles()[0]);

    expect(page.articles()[0].is_read).toBe(false);
  });

  it('rolls back the optimistic is_bookmarked flip on failure', () => {
    const page = setup();
    me.addBookmark = () => throwError(() => new Error('boom'));

    page.toggleBookmark(page.articles()[0]);

    expect(page.articles()[0].is_bookmarked).toBe(false);
  });

  it('ignores a toggle while one is already pending for that article', () => {
    const page = setup();
    const pending = new Subject<void>();
    let calls = 0;
    me.markRead = () => {
      calls++;
      return pending;
    };

    page.toggleRead(page.articles()[0]); // now pending — markRead not yet resolved
    expect(page.isReadPending('article-1')).toBe(true);
    page.toggleRead(page.articles()[0]); // ignored: a toggle is already in flight

    expect(calls).toBe(1);

    pending.next();
    pending.complete();
    expect(page.isReadPending('article-1')).toBe(false);
  });
});
