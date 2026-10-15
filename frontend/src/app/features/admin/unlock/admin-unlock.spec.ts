import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter, Router, UrlTree } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { vi, Mock } from 'vitest';
import { AdminUnlock } from './admin-unlock';
import { AuthService } from '../../../services/auth';
import { ManageSessionService } from '../../../services/manage-session';
interface Page {
  email: string;
  password: string;
  code: string;
  submit(): Promise<void>;
  resend(): Promise<void>;
  challenge(): { challenge_id: string } | null;
  busy(): boolean;
  error(): string;
}
describe('management password and Telegram login', () => {
  let signIn: ReturnType<typeof vi.fn>;
  let request: ReturnType<typeof vi.fn>;
  let verify: ReturnType<typeof vi.fn>;
  let navigate: Mock<(url: string | UrlTree) => Promise<boolean>>;
  let redirect: string;
  beforeEach(() => {
    signIn = vi.fn().mockResolvedValue({ error: null });
    request = vi
      .fn()
      .mockResolvedValue({ challenge_id: 'challenge', expires_in: 300, retry_after: 60 });
    verify = vi.fn().mockResolvedValue(undefined);
    navigate = vi.fn<(url: string | UrlTree) => Promise<boolean>>().mockResolvedValue(true);
    redirect = '/manage/feeds';
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AdminUnlock],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { isConfigured: () => true, signIn } },
        { provide: ManageSessionService, useValue: { request, verify, clear: vi.fn() } },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              get queryParamMap() {
                return convertToParamMap({ redirect });
              },
            },
          },
        },
      ],
    });
    TestBed.inject(Router).navigateByUrl = (url) => navigate(url);
  });
  function setup() {
    const fixture = TestBed.createComponent(AdminUnlock);
    fixture.detectChanges();
    const page = fixture.componentInstance as unknown as Page;
    page.email = 'owner@example.com';
    page.password = 'test-password';
    return { fixture, page };
  }
  it('does not navigate until both the password and OTP succeed', async () => {
    const { page } = setup();
    await page.submit();
    expect(signIn).toHaveBeenCalledWith('owner@example.com', 'test-password');
    expect(page.password).toBe('');
    expect(request).toHaveBeenCalledOnce();
    expect(navigate).not.toHaveBeenCalled();
    page.code = '012345';
    await page.submit();
    expect(verify).toHaveBeenCalledWith('challenge', '012345');
    expect(navigate).toHaveBeenCalledWith('/manage/feeds');
  });
  it('does not send OTP after an incorrect password', async () => {
    signIn.mockResolvedValue({ error: '密碼錯誤' });
    const { page } = setup();
    await page.submit();
    expect(request).not.toHaveBeenCalled();
    expect(page.error()).toBe('密碼錯誤');
    expect(page.busy()).toBe(false);
  });
  it('keeps the challenge after an incorrect code', async () => {
    const { page } = setup();
    await page.submit();
    page.code = '123456';
    verify.mockRejectedValue(
      new HttpErrorResponse({ status: 403, error: { detail: '驗證碼錯誤' } }),
    );
    await page.submit();
    expect(page.challenge()).not.toBeNull();
    expect(page.error()).toBe('驗證碼錯誤');
    expect(navigate).not.toHaveBeenCalled();
  });
  it('prevents resending before the cooldown elapses', async () => {
    const { page } = setup();
    await page.submit();
    await page.resend();
    expect(request).toHaveBeenCalledOnce();
  });
  it('returns to the original OAuth request after Telegram verification', async () => {
    redirect = '/manage/mcp-authorize?request=opaque-request';
    const { page } = setup();
    await page.submit();
    page.code = '123456';
    await page.submit();
    expect(navigate).toHaveBeenCalledWith(redirect);
  });
  it('rejects redirects outside /manage', async () => {
    redirect = '/manage-attacker';
    const { page } = setup();
    await page.submit();
    page.code = '123456';
    await page.submit();
    expect(navigate).toHaveBeenCalledWith('/manage/dashboard');
  });
});
