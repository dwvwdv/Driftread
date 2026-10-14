import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { vi } from 'vitest';
import { ManageSessionService } from './manage-session';
import { isReviewOAuthRedirect, reviewOAuthError, ReviewOAuthService } from './review-oauth';

describe('source review OAuth administration', () => {
  let mock: HttpTestingController;
  let identity: string;
  let clear: ReturnType<typeof vi.fn>;
  let navigate: ReturnType<typeof vi.fn<Router['navigate']>>;
  beforeEach(() => {
    identity = 'owner';
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
            headers: () => ({ Authorization: `Bearer ${identity}`, 'x-manage-session': identity }),
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
  it('discards a consent response after the account changes', () => {
    let value: unknown;
    let error: unknown;
    TestBed.inject(ReviewOAuthService)
      .decide('req', true)
      .subscribe({ next: (v) => (value = v), error: (e) => (error = e) });
    identity = 'other';
    mock
      .expectOne((r) => r.method === 'POST')
      .flush({ redirect_url: 'https://chatgpt.com/connector_platform_oauth_redirect?code=old' });
    expect(value).toBeUndefined();
    expect(reviewOAuthError(error)).toContain('帳號已變更');
  });
  it('does not clear a newer session on an older unauthorized response', () => {
    TestBed.inject(ReviewOAuthService)
      .grants()
      .subscribe({ error: () => {} });
    identity = 'other';
    mock
      .expectOne((r) => r.url.endsWith('/grants'))
      .flush({}, { status: 401, statusText: 'Unauthorized' });
    expect(clear).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });
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
