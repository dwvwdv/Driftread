import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { of, Subject, throwError } from 'rxjs';
import { vi } from 'vitest';
import { ReviewOAuthConsent, ReviewOAuthService } from '../../../services/review-oauth';
import { AdminMcpAuthorize } from './admin-mcp-authorize';

interface Page {
  decide(approved: boolean): Promise<void>;
  navigate(url: string): void;
  busy(): boolean;
  error(): string;
  consent(): ReviewOAuthConsent | null;
}
describe('management OAuth consent page', () => {
  const id = 'opaque-request-123456';
  let consent: ReturnType<typeof vi.fn>;
  let decide: ReturnType<typeof vi.fn>;
  let request: string;
  const dto = () => ({
    request_id: id,
    client_name: '<img src=x onerror=alert(1)>',
    redirect_host: 'chatgpt.com',
    scope: 'source:review',
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  beforeEach(() => {
    request = id;
    consent = vi.fn().mockReturnValue(of(dto()));
    decide = vi
      .fn()
      .mockReturnValue(
        of({
          redirect_url:
            'https://chatgpt.com/connector_platform_oauth_redirect?code=opaque&state=s%2Fx',
        }),
      );
    TestBed.configureTestingModule({
      imports: [AdminMcpAuthorize],
      providers: [
        provideRouter([]),
        { provide: ReviewOAuthService, useValue: { consent, decide } },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              get queryParamMap() {
                return convertToParamMap({ request });
              },
            },
          },
        },
      ],
    });
  });
  async function setup() {
    const fixture = TestBed.createComponent(AdminMcpAuthorize);
    const page = fixture.componentInstance as unknown as Page;
    const navigate = vi.spyOn(page, 'navigate').mockImplementation(() => {});
    fixture.detectChanges();
    await Promise.resolve();
    fixture.detectChanges();
    return { fixture, page, navigate };
  }
  it('renders the client as text and explains automatic admission', async () => {
    const { fixture } = await setup();
    expect(fixture.nativeElement.querySelector('img')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(fixture.nativeElement.textContent).toContain('自動收錄');
  });
  it('returns the server callback unchanged after approval', async () => {
    const { page, navigate } = await setup();
    await page.decide(true);
    expect(decide).toHaveBeenCalledWith(id, true);
    expect(navigate).toHaveBeenCalledWith(
      'https://chatgpt.com/connector_platform_oauth_redirect?code=opaque&state=s%2Fx',
    );
  });
  it('returns the server denial callback', async () => {
    decide.mockReturnValue(
      of({ redirect_url: 'https://chatgpt.com/connector/oauth/cb?error=access_denied&state=s' }),
    );
    const { page, navigate } = await setup();
    await page.decide(false);
    expect(decide).toHaveBeenCalledWith(id, false);
    expect(navigate).toHaveBeenCalledOnce();
  });
  it('blocks duplicate consent submissions', async () => {
    const response = new Subject<{ redirect_url: string }>();
    decide.mockReturnValue(response);
    const { page } = await setup();
    const pending = page.decide(true);
    await page.decide(false);
    expect(decide).toHaveBeenCalledOnce();
    expect(page.busy()).toBe(true);
    response.next({ redirect_url: 'https://chatgpt.com/connector_platform_oauth_redirect?code=c' });
    await pending;
  });
  it('never navigates to a malicious callback', async () => {
    decide.mockReturnValue(of({ redirect_url: 'https://chatgpt.com.evil.test/callback?code=c' }));
    const { page, navigate } = await setup();
    await page.decide(true);
    expect(navigate).not.toHaveBeenCalled();
    expect(page.error()).toContain('安全限制');
    expect(page.consent()).toBeNull();
  });
  it('rejects an expired request', async () => {
    consent.mockReturnValue(of({ ...dto(), expires_at: '2000-01-01T00:00:00Z' }));
    const { page } = await setup();
    expect(page.consent()).toBeNull();
    await page.decide(true);
    expect(decide).not.toHaveBeenCalled();
  });
  it('accepts the optional offline refresh permission', async () => {
    consent.mockReturnValue(of({ ...dto(), scope: 'source:review offline_access' }));
    const { page } = await setup();
    expect(page.consent()).not.toBeNull();
  });
  it('rejects mismatched request IDs and excessive scopes', async () => {
    consent.mockReturnValue(of({ ...dto(), request_id: 'wrong-request' }));
    const { page } = await setup();
    expect(page.consent()).toBeNull();
  });
  it('rejects a scope beyond source review', async () => {
    consent.mockReturnValue(of({ ...dto(), scope: 'source:review admin:settings' }));
    const { page } = await setup();
    expect(page.consent()).toBeNull();
  });
  it('does not navigate for a denied management identity', async () => {
    decide.mockReturnValue(throwError(() => new HttpErrorResponse({ status: 403 })));
    const { page, navigate } = await setup();
    await page.decide(true);
    expect(navigate).not.toHaveBeenCalled();
    expect(page.error()).toContain('無權');
  });
  it('does not issue an API call with an invalid request', async () => {
    request = 'https://evil.test';
    const { page } = await setup();
    expect(consent).not.toHaveBeenCalled();
    expect(page.error()).toContain('無效');
  });
  it('ignores approval completion after leaving the page', async () => {
    const response = new Subject<{ redirect_url: string }>();
    decide.mockReturnValue(response);
    const { page, fixture, navigate } = await setup();
    const pending = page.decide(true);
    fixture.destroy();
    response.next({ redirect_url: 'https://chatgpt.com/connector_platform_oauth_redirect?code=c' });
    await pending;
    expect(navigate).not.toHaveBeenCalled();
  });
});
