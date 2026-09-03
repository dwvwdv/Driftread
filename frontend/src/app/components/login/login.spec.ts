import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, Subject, of, throwError } from 'rxjs';
import { Login } from './login';
import { AuthService } from '../../services/auth';
import { DiscoverService } from '../../services/discover';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';
import { Feed } from '../../models';

describe('Login redirect after sign-in', () => {
  let auth: {
    isConfigured: () => boolean;
    signIn: (e: string, p: string) => Promise<{ error: string | null }>;
    session: () => { user: { id: string } } | null;
  };
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

  const PENDING_KEY = 'driftread:pendingImportFeedUrl';

  /** Mirrors pending-import.ts's stash format, with a caller-chosen nonce. */
  function stashImport(url: string, nonce: string): void {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify({ url, nonce }));
  }

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
      session: () => ({ user: { id: 'user-1' } }),
    };
    importResult = undefined;
    sessionStorage.clear();
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

  it('resumes an import stashed by Discover and marks the result subscribed', async () => {
    queryParams = { redirect: '/discover', importNonce: 'nonce-1' };
    const page = setup();
    stashImport('https://example.com/feed.xml', 'nonce-1');

    await page.submit();

    expect(discover.importByUrlCalls).toEqual(['https://example.com/feed.xml']);
    expect(subs.markSubscribedCalls).toEqual(['feed-1']);
    expect(navigateCalls).toEqual(['/discover']);
    expect(danger).toEqual([]);
    // Read-once: a stale value must not resume again on a later login.
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('does not act on importFeedUrl passed as a query param', async () => {
    // Codex review on PR #52 (round 2): a crafted /login?importFeedUrl=<url>
    // link must not trigger an import — only Discover.importFeed()'s own
    // sessionStorage stash (an actual click) can, and only via the matching
    // nonce it handed out, not this now-unused legacy param name.
    queryParams = { redirect: '/discover', importFeedUrl: 'https://attacker.example/feed.xml' };
    const page = setup();

    await page.submit();

    expect(discover.importByUrlCalls).toEqual([]);
  });

  it('does not resume a stashed import when the login was not reached via its redirect', async () => {
    // Codex review on PR #52 (round 3): a reader who clicks Import, backs out
    // of /login without submitting, and later signs in for something
    // unrelated must not have the abandoned import silently resumed — no
    // importNonce in the query string means this login didn't come from that
    // redirect at all.
    queryParams = { redirect: '/me/feeds' };
    const page = setup();
    stashImport('https://example.com/feed.xml', 'nonce-1');

    await page.submit();

    expect(discover.importByUrlCalls).toEqual([]);
    expect(navigateCalls).toEqual(['/me/feeds']);
  });

  it('does not resume a stashed import when the nonce does not match', async () => {
    queryParams = { redirect: '/discover', importNonce: 'stale-nonce' };
    const page = setup();
    // A later click overwrote the stash with a fresh nonce before this
    // (older) login attempt landed.
    stashImport('https://example.com/feed.xml', 'current-nonce');

    await page.submit();

    expect(discover.importByUrlCalls).toEqual([]);
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull();
  });

  it('toasts an error without blocking the redirect when the resumed import fails', async () => {
    queryParams = { redirect: '/discover', importNonce: 'nonce-1' };
    const page = setup();
    stashImport('https://example.com/feed.xml', 'nonce-1');
    importResult = throwError(() => new Error('boom'));

    await page.submit();

    expect(subs.markSubscribedCalls).toEqual([]);
    expect(danger.length).toBe(1);
    expect(navigateCalls).toEqual(['/discover']);
  });

  it('does not mark subscribed if the signed-in identity changed before the import resolved', async () => {
    queryParams = { redirect: '/discover', importNonce: 'nonce-1' };
    const page = setup();
    stashImport('https://example.com/feed.xml', 'nonce-1');
    const pending = new Subject<Feed>();
    importResult = pending;

    await page.submit();
    auth.session = () => ({ user: { id: 'user-2' } }); // switches before the response returns
    pending.next(feed);
    pending.complete();

    // The backend created this subscription for user-1, not whoever is
    // signed in when the (slow) response happens to arrive.
    expect(subs.markSubscribedCalls).toEqual([]);
  });
});
