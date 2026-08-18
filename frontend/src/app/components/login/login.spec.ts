import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Login } from './login';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { ToastService } from '../../ui/toast/toast';

describe('Login redirect after sign-in', () => {
  let auth: { isConfigured: () => boolean; signIn: (e: string, p: string) => Promise<{ error: string | null }> };
  let subs: { calls: string[]; subscribeCalls: string[]; syncIdentity: () => void; subscribe: (id: string) => void };
  let navigateCalls: string[];
  let queryParams: Record<string, string>;

  function setup() {
    auth = {
      isConfigured: () => true,
      signIn: async () => ({ error: null }),
    };
    subs = {
      calls: [],
      subscribeCalls: [],
      syncIdentity: () => subs.calls.push('syncIdentity'),
      subscribe: (id) => {
        subs.calls.push('subscribe');
        subs.subscribeCalls.push(id);
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
        {
          provide: ToastService,
          useValue: { info: () => {}, danger: () => {}, success: () => {}, warning: () => {} },
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
});
