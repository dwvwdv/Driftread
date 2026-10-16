import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { vi } from 'vitest';
import { ManageSessionService } from './manage-session';
import { isReviewOAuthRedirect, reviewOAuthError, ReviewOAuthService } from './review-oauth';

describe('source review OAuth administration', () => {
  let mock: HttpTestingController;
  let userId: string | null;
  let authSessionId: string;
  let accessToken: string;
  let manageToken: string;
  let clear: ReturnType<typeof vi.fn>;
  let navigate: ReturnType<typeof vi.fn<Router['navigate']>>;
  beforeEach(() => {
    userId = 'owner';
    authSessionId = 'login-1';
    accessToken = 'owner';
    manageToken = 'owner';
    clear = vi.fn();
    navigate = vi.fn<Router['navigate']>().mockResolvedValue(true);
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ManageSessionService,
          useValue: {
            identity: () => (userId ? { userId, authSessionId } : null),
            headers: () => ({
              Authorization: `Bearer ${accessToken}`,
              'x-manage-session': manageToken,
            }),
            clear,
          },
        },
      ],
    });
    TestBed.inject(Router).navigate = navigate;
    mock = TestBed.inject(HttpTestingController);
  });
  afterEach(() => mock.verify());
  it('uses management headers and typed consent, listing and revoke requests', () => {
    const service = TestBed.inject(ReviewOAuthService);
    service.consent('req').subscribe();
    service.decide('req', true).subscribe();
    service.grants().subscribe();
    service.revoke('grant').subscribe();
    for (const [path, method, body] of [
      ['/consent/req', 'GET', null],
      ['/consent/req', 'POST', { approved: true }],
      ['/grants', 'GET', null],
      ['/grants/grant', 'DELETE', null],
    ] as const) {
      const req = mock.expectOne(
        (r) => r.url.endsWith(`/review-oauth${path}`) && r.method === method,
      );
      expect(req.request.body).toEqual(body);
      expect(req.request.headers.get('Authorization')).toBe('Bearer owner');
      expect(req.request.headers.get('x-manage-session')).toBe('owner');
      expect(req.request.headers.has('X-API-Key')).toBe(false);
      req.flush(method === 'DELETE' ? null : {});
    }
  });
  it.each([null, '2029-01-01T00:00:00.000Z'])(
    'patches a grant expiry with management authentication (%s)',
    (expires_at) => {
      let result: unknown;
      TestBed.inject(ReviewOAuthService)
        .updateExpiry('grant/id', expires_at)
        .subscribe((value) => (result = value));
      const request = mock.expectOne((r) => r.url.endsWith('/review-oauth/grants/grant%2Fid'));
      expect(request.request.method).toBe('PATCH');
      expect(request.request.body).toEqual({ expires_at });
      expect(request.request.headers.get('Authorization')).toBe('Bearer owner');
      expect(request.request.headers.get('x-manage-session')).toBe('owner');
      const updated = { id: 'grant/id', expires_at };
      request.flush(updated);
      expect(result).toEqual(updated);
    },
  );

  it('accepts an expiry update after JWT refresh within the same login', () => {
    let value: unknown;
    TestBed.inject(ReviewOAuthService)
      .updateExpiry('grant', null)
      .subscribe((result) => (value = result));
    accessToken = 'refreshed';
    mock.expectOne((r) => r.method === 'PATCH').flush({ id: 'grant', expires_at: null });
    expect(value).toEqual({ id: 'grant', expires_at: null });
    expect(clear).not.toHaveBeenCalled();
  });

  it.each(['account', 'auth session', 'management session', 'sign out'])(
    'discards an expiry write response after a change to the %s',
    (change) => {
      let value: unknown;
      let error: unknown;
      TestBed.inject(ReviewOAuthService)
        .updateExpiry('grant', null)
        .subscribe({ next: (result) => (value = result), error: (reason) => (error = reason) });
      if (change === 'account') userId = 'other';
      if (change === 'auth session') authSessionId = 'login-2';
      if (change === 'management session') manageToken = 'new';
      if (change === 'sign out') userId = null;
      mock.expectOne((r) => r.method === 'PATCH').flush({ id: 'grant', expires_at: null });
      expect(value).toBeUndefined();
      expect(reviewOAuthError(error)).toContain('帳號已變更');
    },
  );

  it.each(['account', 'auth session', 'management session', 'refreshed JWT'])(
    'preserves a newer %s after an older expiry PATCH returns 401',
    (change) => {
      TestBed.inject(ReviewOAuthService)
        .updateExpiry('grant', null)
        .subscribe({ error: () => {} });
      if (change === 'account') userId = 'other';
      if (change === 'auth session') authSessionId = 'login-2';
      if (change === 'management session') manageToken = 'new';
      if (change === 'refreshed JWT') accessToken = 'refreshed';
      mock
        .expectOne((r) => r.method === 'PATCH')
        .flush({}, { status: 401, statusText: 'Unauthorized' });
      expect(clear).not.toHaveBeenCalled();
      expect(navigate).not.toHaveBeenCalled();
    },
  );

  it('returns to management login when the current expiry PATCH session is unauthorized', () => {
    TestBed.inject(ReviewOAuthService)
      .updateExpiry('grant', null)
      .subscribe({ error: () => {} });
    mock
      .expectOne((r) => r.method === 'PATCH')
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(clear).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledOnce();
  });

  it('keeps the OAuth query when a management session expires', () => {
    const router = TestBed.inject(Router);
    vi.spyOn(router, 'url', 'get').mockReturnValue('/manage/mcp-authorize?request=opaque');
    TestBed.inject(ReviewOAuthService)
      .consent('req')
      .subscribe({ error: () => {} });
    mock
      .expectOne((r) => r.url.endsWith('/consent/req'))
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(clear).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith(['/manage/login'], {
      queryParams: { redirect: '/manage/mcp-authorize?request=opaque' },
    });
  });
  it('accepts the consent callback after the JWT refreshes within the same login', () => {
    let value: unknown;
    let error: unknown;
    TestBed.inject(ReviewOAuthService)
      .decide('req', true)
      .subscribe({ next: (v) => (value = v), error: (e) => (error = e) });
    accessToken = 'refreshed-jwt';
    const callback = {
      redirect_url: 'https://chatgpt.com/connector_platform_oauth_redirect?code=approved',
    };
    mock.expectOne((r) => r.method === 'POST').flush(callback);
    expect(value).toEqual(callback);
    expect(error).toBeUndefined();
    expect(clear).not.toHaveBeenCalled();
  });
  it.each(['account', 'auth session', 'management session', 'sign out'])(
    'discards a consent response after a change to the %s',
    (change) => {
      let value: unknown;
      let error: unknown;
      TestBed.inject(ReviewOAuthService)
        .decide('req', true)
        .subscribe({ next: (v) => (value = v), error: (e) => (error = e) });
      if (change === 'account') userId = 'other';
      if (change === 'auth session') authSessionId = 'login-2';
      if (change === 'management session') manageToken = 'verified-again';
      if (change === 'sign out') userId = null;
      mock
        .expectOne((r) => r.method === 'POST')
        .flush({ redirect_url: 'https://chatgpt.com/connector_platform_oauth_redirect?code=old' });
      expect(value).toBeUndefined();
      expect(reviewOAuthError(error)).toContain('帳號已變更');
    },
  );
  it.each(['account', 'auth session', 'management session', 'refreshed JWT'])(
    'does not clear a newer %s on an older unauthorized response',
    (change) => {
      TestBed.inject(ReviewOAuthService)
        .grants()
        .subscribe({ error: () => {} });
      if (change === 'account') userId = 'other';
      if (change === 'auth session') authSessionId = 'login-2';
      if (change === 'management session') manageToken = 'verified-again';
      if (change === 'refreshed JWT') accessToken = 'refreshed-jwt';
      mock
        .expectOne((r) => r.url.endsWith('/grants'))
        .flush({}, { status: 401, statusText: 'Unauthorized' });
      expect(clear).not.toHaveBeenCalled();
      expect(navigate).not.toHaveBeenCalled();
    },
  );
});

describe('OAuth callback destination', () => {
  it.each([
    'https://chatgpt.com/connector_platform_oauth_redirect?code=opaque&state=a%2Fb',
    'https://chatgpt.com/connector/oauth/callback_123-foo?error=access_denied&state=s',
  ])('accepts the official callback %s', (url) => expect(isReviewOAuthRedirect(url)).toBe(true));
  it.each([
    'https://chatgpt.com.evil.example/connector_platform_oauth_redirect',
    'https://evil.example/?url=https://chatgpt.com',
    'http://chatgpt.com/connector_platform_oauth_redirect',
    'https://owner@chatgpt.com/connector_platform_oauth_redirect',
    'https://chatgpt.com:444/connector_platform_oauth_redirect',
    'https://chatgpt.com:443/connector_platform_oauth_redirect',
    'https://chatgpt.com/connector_platform_oauth_redirect#token=secret',
    'https://chatgpt.com/connector/oauth/id/other',
    'https://chatgpt.com/connector_platform/oauth/callback',
    'javascript:alert(1)',
    '//chatgpt.com/connector_platform_oauth_redirect',
  ])('rejects an unsafe callback %s', (url) => expect(isReviewOAuthRedirect(url)).toBe(false));
  it('does not expose raw backend error details', () => {
    expect(
      reviewOAuthError(new HttpErrorResponse({ status: 500, error: { detail: 'secret' } })),
    ).not.toContain('secret');
  });
});
