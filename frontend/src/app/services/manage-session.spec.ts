import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Session } from '@supabase/supabase-js';
import { AuthService } from './auth';
import { ManageSessionService } from './manage-session';

const KEY = 'driftread_manage_session';
function login(userId = 'owner', sessionId = 'login-1'): Session {
  return {
    user: { id: userId },
    access_token: `header.${btoa(JSON.stringify({ session_id: sessionId }))}.signature`,
  } as Session;
}
describe('ManageSessionService', () => {
  const session = signal<Session | null>(null);
  let http: HttpTestingController;
  beforeEach(() => {
    sessionStorage.clear();
    session.set(login());
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: AuthService,
          useValue: {
            session,
            get accessToken() {
              return session()?.access_token ?? null;
            },
          },
        },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());
  function saved(expires = Date.now() + 3600000): void {
    sessionStorage.setItem(
      KEY,
      JSON.stringify({
        token: 'verified',
        expires_at: new Date(expires).toISOString(),
        userId: 'owner',
        authSessionId: 'login-1',
      }),
    );
  }
  it('removes the legacy shared key and starts locked without a Telegram session', () => {
    sessionStorage.setItem('driftread_admin_key', 'old-api-key');
    expect(TestBed.inject(ManageSessionService).token()).toBeNull();
    expect(sessionStorage.getItem('driftread_admin_key')).toBeNull();
  });
  it('restores a verified session and invalidates it on account switch or sign out', () => {
    saved();
    const service = TestBed.inject(ManageSessionService);
    expect(service.token()).toBe('verified');
    session.set(login('other'));
    expect(service.token()).toBeNull();
    session.set(null);
    expect(service.token()).toBeNull();
  });
  it('invalidates a session after a new password login', () => {
    saved();
    const service = TestBed.inject(ManageSessionService);
    session.set(login('owner', 'login-2'));
    expect(service.token()).toBeNull();
  });
  it('does not accept expired stored sessions', () => {
    saved(Date.now() - 1);
    expect(TestBed.inject(ManageSessionService).token()).toBeNull();
  });
  it('persists only after successful OTP verification and sends both credentials', async () => {
    const service = TestBed.inject(ManageSessionService);
    const promise = service.verify('challenge', '012345');
    const req = http.expectOne((r) => r.url.endsWith('/manage/auth/verify'));
    expect(req.request.headers.get('Authorization')).toBe(`Bearer ${session()!.access_token}`);
    expect(req.request.body).toEqual({ challenge_id: 'challenge', code: '012345' });
    expect(service.token()).toBeNull();
    req.flush({ token: 'verified', expires_at: new Date(Date.now() + 3600000).toISOString() });
    await promise;
    expect(service.token()).toBe('verified');
    expect(service.headers()['x-manage-session']).toBe('verified');
    expect(service.headers()['x-api-key']).toBeUndefined();
    expect(JSON.parse(sessionStorage.getItem(KEY)!).token).toBe('verified');
  });
  it('discards verification completed after an account switch', async () => {
    const service = TestBed.inject(ManageSessionService);
    const promise = service.verify('challenge', '012345');
    session.set(login('other', 'other-login'));
    http
      .expectOne((r) => r.url.endsWith('/manage/auth/verify'))
      .flush({ token: 'stale', expires_at: new Date(Date.now() + 3600000).toISOString() });
    await expect(promise).rejects.toThrow('登入帳號已變更');
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });
  it('revokes the server session and clears storage when logging out', async () => {
    saved();
    const service = TestBed.inject(ManageSessionService);
    const promise = service.logout();
    const req = http.expectOne((r) => r.url.endsWith('/manage/auth/logout'));
    expect(req.request.headers.get('x-manage-session')).toBe('verified');
    req.flush(null, { status: 204, statusText: 'No Content' });
    await promise;
    expect(service.token()).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });
});
