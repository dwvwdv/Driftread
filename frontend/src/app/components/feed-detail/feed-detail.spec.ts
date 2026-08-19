import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { FeedDetail } from './feed-detail';
import { FeedService } from '../../services/feed';
import { RecommendationService } from '../../services/recommendation';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { FeedWithArticles } from '../../models';

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
