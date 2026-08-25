import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { FeedList } from './feed-list';
import { FeedService } from '../../services/feed';
import { SubscriptionService } from '../../services/subscription';
import { AuthService } from '../../services/auth';
import { ToastService } from '../../ui/toast/toast';
import { Feed, PaginatedFeeds } from '../../models';

const feed = (id: string): Feed => ({
  id,
  title: `Feed ${id}`,
  url: `https://example.com/${id}.xml`,
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
});

describe('FeedList quick-subscribe', () => {
  // Signed in by default; a test that needs signed-out sets this to null
  // *before* calling setup() — setup() itself must not touch it, or it would
  // clobber that override right back to signed-in.
  let session: { user: { id: string } } | null = { user: { id: 'user-1' } };
  let subs: {
    subscribeCalls: string[];
    isSubscribed: (id: string) => boolean;
    isPending: (id: string) => boolean;
    subscribe: (id: string) => void;
  };
  let navCalls: unknown[][];

  beforeEach(() => {
    session = { user: { id: 'user-1' } };
  });

  function setup() {
    subs = {
      subscribeCalls: [],
      isSubscribed: () => false,
      isPending: () => false,
      subscribe: (id) => subs.subscribeCalls.push(id),
    };

    const page: PaginatedFeeds = { items: [feed('a')], total: 1, page: 1, page_size: 20 };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [FeedList],
      providers: [
        provideRouter([]),
        {
          provide: FeedService,
          useValue: {
            getFeeds: () => of(page),
            getCategories: () => of([]),
            getLanguages: () => of([]),
          },
        },
        { provide: SubscriptionService, useValue: subs },
        { provide: AuthService, useValue: { session: () => session } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(FeedList);
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
    const component = setup();

    component.quickSubscribe(feed('a'));

    expect(navCalls).toEqual([[['/login'], { queryParams: { redirect: '/', subscribeFeed: 'a' } }]]);
    expect(subs.subscribeCalls).toEqual([]);
  });

  it('subscribes a signed-in reader directly', () => {
    const component = setup();

    component.quickSubscribe(feed('a'));

    expect(subs.subscribeCalls).toEqual(['a']);
  });
});

describe('FeedList combined category/language/tag filtering', () => {
  let getFeedsCalls: unknown[][];

  function setup() {
    const page: PaginatedFeeds = { items: [feed('a')], total: 1, page: 1, page_size: 20 };
    getFeedsCalls = [];

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [FeedList],
      providers: [
        provideRouter([]),
        {
          provide: FeedService,
          useValue: {
            getFeeds: (...args: unknown[]) => {
              getFeedsCalls.push(args);
              return of(page);
            },
            getCategories: () => of([]),
            getLanguages: () => of([]),
          },
        },
        { provide: SubscriptionService, useValue: { isSubscribed: () => false, isPending: () => false } },
        { provide: AuthService, useValue: { session: () => null } },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(FeedList);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  it('sends the picked category, language and clicked tag to getFeeds', () => {
    const component = setup();
    getFeedsCalls = []; // drop the initial ngOnInit load

    component.category = 'News';
    component.language = 'en';
    component.filterByTag('security');

    // filterByTag itself triggers the reload — page, pageSize, category, language, tag, search.
    expect(getFeedsCalls).toEqual([[1, 20, 'News', 'en', 'security', undefined]]);
    expect(component.tag).toBe('security');
  });

  it('clicking the same tag again clears it (toggle)', () => {
    const component = setup();

    component.filterByTag('security');
    expect(component.tag).toBe('security');

    component.filterByTag('security');
    expect(component.tag).toBe('');
  });

  it('counts language and tag towards hasFilters, and clearFilters resets both', () => {
    const component = setup();

    component.language = 'en';
    expect(component.hasFilters).toBe(true);

    component.language = '';
    component.tag = 'security';
    expect(component.hasFilters).toBe(true);

    component.clearFilters();
    expect(component.language).toBe('');
    expect(component.tag).toBe('');
    expect(component.hasFilters).toBe(false);
  });
});
