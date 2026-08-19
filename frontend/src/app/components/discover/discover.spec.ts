import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { Discover } from './discover';
import { DiscoverService } from '../../services/discover';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';
import { Feed } from '../../models';

const feed: Feed = {
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
};

describe('Discover subscribe actions', () => {
  // Signed in by default; a test that needs signed-out sets this to null
  // *before* calling setup() — setup() itself must not touch it, or it would
  // clobber that override right back to signed-in.
  let session: { user: { id: string } } | null = { user: { id: 'user-1' } };
  let subs: {
    subscribeCalls: string[];
    markSubscribedCalls: string[];
    isSubscribed: (id: string) => boolean;
    isPending: (id: string) => boolean;
    subscribe: (id: string) => void;
    markSubscribed: (id: string) => void;
  };
  let navCalls: unknown[][];

  beforeEach(() => {
    session = { user: { id: 'user-1' } };
  });

  function setup(importByUrl?: () => ReturnType<DiscoverService['importByUrl']>) {
    subs = {
      subscribeCalls: [],
      markSubscribedCalls: [],
      isSubscribed: () => false,
      isPending: () => false,
      subscribe: (id) => subs.subscribeCalls.push(id),
      markSubscribed: (id) => subs.markSubscribedCalls.push(id),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Discover],
      providers: [
        provideRouter([]),
        { provide: DiscoverService, useValue: { importByUrl: importByUrl ?? (() => of(feed)) } },
        { provide: AuthService, useValue: { session: () => session } },
        { provide: SubscriptionService, useValue: subs },
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
        },
      ],
    });

    const fixture = TestBed.createComponent(Discover);
    fixture.detectChanges();

    navCalls = [];
    const router = TestBed.inject(Router);
    router.navigate = (commands: any, extras?: any) => {
      navCalls.push([commands, extras]);
      return Promise.resolve(true);
    };

    return fixture.componentInstance;
  }

  it('subscribeExisting sends a signed-out reader to log in first', () => {
    session = null;
    const page = setup();

    page.subscribeExisting('feed-1');

    expect(navCalls).toEqual([
      [['/login'], { queryParams: { redirect: '/discover', subscribeFeed: 'feed-1' } }],
    ]);
    expect(subs.subscribeCalls).toEqual([]);
  });

  it('subscribeExisting subscribes a signed-in reader directly', () => {
    const page = setup();

    page.subscribeExisting('feed-1');

    expect(subs.subscribeCalls).toEqual(['feed-1']);
  });

  it('marks the imported feed as subscribed for a signed-in importer, without a second API call', () => {
    const page = setup();

    page.importFeed({
      feed_url: feed.url,
      title: feed.title,
      website_url: null,
      already_exists: false,
      existing_feed_id: null,
    });

    expect(subs.markSubscribedCalls).toEqual(['feed-1']);
    expect(subs.subscribeCalls).toEqual([]);
  });

  it('does not mark subscribed for a signed-out import', () => {
    session = null;
    const page = setup();

    page.importFeed({
      feed_url: feed.url,
      title: feed.title,
      website_url: null,
      already_exists: false,
      existing_feed_id: null,
    });

    expect(subs.markSubscribedCalls).toEqual([]);
  });

  it('does not mark subscribed for an import that resolves after switching accounts', () => {
    const pending = new Subject<Feed>();
    const page = setup(() => pending);

    page.importFeed({
      feed_url: feed.url,
      title: feed.title,
      website_url: null,
      already_exists: false,
      existing_feed_id: null,
    });

    session = { user: { id: 'user-2' } }; // switches before the response returns
    pending.next(feed);
    pending.complete();

    // The backend created this subscription for user-1, not whoever is
    // signed in when the (slow) response happens to arrive.
    expect(subs.markSubscribedCalls).toEqual([]);
  });
});
