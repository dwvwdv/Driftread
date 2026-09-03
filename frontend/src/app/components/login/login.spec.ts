import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { Login } from './login';
import { AuthService } from '../../services/auth';
import { DiscoverService } from '../../services/discover';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';
import { Feed } from '../../models';

describe('Login redirect after sign-in', () => {
  let auth: { isConfigured: () => boolean; signIn: (e: string, p: string) => Promise<{ error: string | null }> };
  let subs: {
    calls: string[];
    subscribeCalls: string[];
    markSubscribedCalls: string[];
    syncIdentity: () => void;
    subscribe: (id: string) => void;
    markSubscribed: (id: string) => void;
  };
  let discover: { importByUrlCalls: string[]; importByUrl: (url: string) => Observable<Feed> };
  let danger: string[];
  let navigateCalls: string[];
  let queryParams: Record<string, string>;
  let importResult: Observable<Feed> | undefined;

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

  function setup() {
    auth = {
      isConfigured: () => true,
      signIn: async () => ({ error: null }),
    };
    importResult = undefined;
    subs = {
      calls: [],
      subscribeCalls: [],
      markSubscribedCalls: [],
      syncIdentity: () => subs.calls.push('syncIdentity'),
      subscribe: (id) => {
        subs.calls.push('subscribe');
        subs.subscribeCalls.push(id);
      },
      markSubscribed: (id) => subs.markSubscribedCalls.push(id),
    };
    danger = [];
    discover = {
      importByUrlCalls: [],
      importByUrl: (url: string) => {
        discover.importByUrlCalls.push(url);
        return importResult ?? of(feed);
      },
    };
    navigateCalls = [];

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Login],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap(queryParams) } },
        },
        { provide: AuthService, useValue: auth },
        { provide: SubscriptionService, useValue: subs },
        { provide: DiscoverService, useValue: discover },
        {
          provide: ToastService,
          useValue: {
            info: () => {},
            danger: (msg: string) => danger.push(msg),
            success: () => {},
            warning: () => {},
          },
        },
      ],
    });

    const fixture = TestBed.createComponent(Login);
    fixture.detectChanges();

    const router = TestBed.inject(Router);
    router.navigateByUrl = (url: any) => {
      navigateCalls.push(String(url));
      return Promise.resolve(true);
    };

    const page = fixture.componentInstance;
    page.email = 'reader@example.com';
    page.password = 'hunter22';
    return page;
  }

  it('completes the pending subscribe and returns to the original feed', async () => {
    queryParams = { redirect: '/feeds/feed-1', subscribeFeed: 'feed-1' };
    const page = setup();

    await page.submit();

    expect(subs.subscribeCalls).toEqual(['feed-1']);
    expect(navigateCalls).toEqual(['/feeds/feed-1']);
    // syncIdentity() must run before subscribe() — see SubscriptionService.
    // syncIdentity: without it, subscribe() can still be tagged with the
    // pre-login identity, since the identity effect is scheduled rather
    // than synchronous with the session write auth.signIn() just made.
    expect(subs.calls).toEqual(['syncIdentity', 'subscribe']);
  });

  it('falls back to home with no redirect param and does not subscribe to anything', async () => {
    queryParams = {};
    const page = setup();

    await page.submit();

    expect(subs.subscribeCalls).toEqual([]);
    expect(navigateCalls).toEqual(['/']);
  });

  it('resumes a deferred import and marks the result subscribed', async () => {
    queryParams = { redirect: '/discover', importFeedUrl: 'https://example.com/feed.xml' };
    const page = setup();

    await page.submit();

    expect(discover.importByUrlCalls).toEqual(['https://example.com/feed.xml']);
    expect(subs.markSubscribedCalls).toEqual(['feed-1']);
    expect(navigateCalls).toEqual(['/discover']);
    expect(danger).toEqual([]);
  });

  it('toasts an error without blocking the redirect when the resumed import fails', async () => {
    queryParams = { redirect: '/discover', importFeedUrl: 'https://example.com/feed.xml' };
    const page = setup();
    importResult = throwError(() => new Error('boom'));

    await page.submit();

    expect(subs.markSubscribedCalls).toEqual([]);
    expect(danger.length).toBe(1);
    expect(navigateCalls).toEqual(['/discover']);
  });
});
