import { TestBed } from '@angular/core/testing';
import { Router, UrlSegment, UrlTree, provideRouter } from '@angular/router';
import { adminGuard } from './admin.guard';
import { ManageSessionService } from '../services/manage-session';
import { AuthService } from '../services/auth';

function run(...paths: string[]): Promise<boolean | UrlTree> {
  return TestBed.runInInjectionContext(
    () =>
      adminGuard(
        {},
        paths.map((p) => new UrlSegment(p, {})),
      ) as Promise<boolean | UrlTree>,
  );
}

describe('adminGuard', () => {
  let token: string | null;
  beforeEach(() => {
    token = null;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { ready: Promise.resolve() } },
        { provide: ManageSessionService, useValue: { token: () => token } },
      ],
    });
  });
  it('allows the console after Telegram verification', async () => {
    token = 'verified';
    expect(await run('manage', 'dashboard')).toBe(true);
  });
  it('redirects to the management login and remembers the destination', async () => {
    const result = await run('manage', 'candidates');
    expect(result).toBeInstanceOf(UrlTree);
    const url = TestBed.inject(Router).serializeUrl(result as UrlTree);
    expect(url).toContain('/manage/login');
    expect(decodeURIComponent(url)).toContain('/manage/candidates');
  });
  it('preserves the OAuth request query through management login', async () => {
    const router = TestBed.inject(Router);
    const destination = router.parseUrl('/manage/mcp-authorize?request=opaque-request');
    router.getCurrentNavigation = () =>
      ({ extractedUrl: destination }) as ReturnType<Router['getCurrentNavigation']>;
    const result = await run('manage', 'mcp-authorize');
    expect(decodeURIComponent(router.serializeUrl(result as UrlTree))).toContain(
      '/manage/mcp-authorize?request=opaque-request',
    );
  });
  it('waits for Supabase session restoration before testing the OTP session', async () => {
    let restore!: () => void;
    TestBed.inject(AuthService).ready = new Promise<void>((resolve) => {
      restore = resolve;
    });
    const pending = run('manage');
    token = 'restored';
    restore();
    expect(await pending).toBe(true);
  });
});
