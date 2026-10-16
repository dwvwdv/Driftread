import { TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { of, Subject, throwError } from 'rxjs';
import { vi } from 'vitest';
import { ReviewOAuthGrant, ReviewOAuthService } from '../../../services/review-oauth';
import { AdminMcpConnections } from './admin-mcp-connections';
interface Page {
  load(): Promise<void>;
  revoke(id: string): Promise<void>;
  grants(): ReviewOAuthGrant[];
  error(): string;
  edit(grant: ReviewOAuthGrant): void;
  cancelEdit(): void;
  saveExpiry(id: string): Promise<void>;
  editing(): string | null;
  saving(): string | null;
  revoking(): string | null;
  draftError(): string;
  permanent: boolean;
  expiry: string;
}
const grant: ReviewOAuthGrant = {
  id: 'grant',
  client_name: 'ChatGPT',
  scope: 'source:review',
  created_at: '2026-10-14T00:00:00Z',
  expires_at: '2027-01-01T00:00:00Z',
};
describe('OAuth connection management', () => {
  let grants: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;
  let updateExpiry: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-16T00:00:00Z'));
    grants = vi.fn().mockReturnValue(of([grant]));
    revoke = vi.fn().mockReturnValue(of(undefined));
    updateExpiry = vi.fn().mockReturnValue(of(grant));
    TestBed.configureTestingModule({
      imports: [AdminMcpConnections],
      providers: [{ provide: ReviewOAuthService, useValue: { grants, revoke, updateExpiry } }],
    });
  });
  afterEach(() => vi.restoreAllMocks());
  async function setup() {
    const fixture = TestBed.createComponent(AdminMcpConnections);
    fixture.detectChanges();
    await Promise.resolve();
    return { fixture, page: fixture.componentInstance as unknown as Page };
  }
  it('loads the list and refreshes after a successful revoke', async () => {
    const { page } = await setup();
    grants.mockReturnValue(of([]));
    await page.revoke('grant');
    expect(revoke).toHaveBeenCalledWith('grant');
    expect(grants).toHaveBeenCalledTimes(2);
    expect(page.grants()).toEqual([]);
  });
  it('shows revoked history and disables revocation for expired or revoked grants', async () => {
    grants.mockReturnValue(
      of([
        { ...grant, revoked_at: '2026-10-14T01:00:00Z' },
        { ...grant, id: 'expired', expires_at: '2000-01-01T00:00:00Z' },
      ]),
    );
    const { page, fixture } = await setup();
    fixture.detectChanges();
    await page.revoke('grant');
    await page.revoke('expired');
    expect(revoke).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('已撤銷');
    expect(fixture.nativeElement.textContent).toContain('已過期');
    const buttons = fixture.nativeElement.querySelectorAll('ob-card button');
    expect([...buttons].every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });
  it('keeps the grant visible when revocation fails', async () => {
    const { page } = await setup();
    revoke.mockReturnValue(throwError(() => new HttpErrorResponse({ status: 500 })));
    await page.revoke('grant');
    expect(page.grants()).toEqual([grant]);
    expect(page.error()).toContain('失敗');
  });
  it('prevents a stale list from resurrecting a revoked grant', async () => {
    const { page } = await setup();
    const old = new Subject<ReviewOAuthGrant[]>();
    grants.mockReturnValueOnce(old);
    const pending = page.load();
    grants.mockReturnValue(of([]));
    await page.revoke('grant');
    old.next([grant]);
    await pending;
    expect(page.grants()).toEqual([]);
  });
  it('prevents duplicate revocations and ignores a completion after leaving', async () => {
    const { page, fixture } = await setup();
    const response = new Subject<void>();
    revoke.mockReturnValue(response);
    const pending = page.revoke('grant');
    await page.revoke('grant');
    expect(revoke).toHaveBeenCalledOnce();
    fixture.destroy();
    response.next();
    await pending;
    expect(grants).toHaveBeenCalledOnce();
  });
  it('renders a permanent grant as active, editable and revocable', async () => {
    const permanent = { ...grant, expires_at: null };
    grants.mockReturnValue(of([permanent]));
    const { page, fixture } = await setup();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('永久有效');
    const buttons = [
      ...fixture.nativeElement.querySelectorAll('ob-card button'),
    ] as HTMLButtonElement[];
    expect(buttons.every((button) => !button.disabled)).toBe(true);
    page.edit(permanent);
    expect(page.permanent).toBe(true);
    await page.revoke('grant');
    expect(revoke).toHaveBeenCalledWith('grant');
  });

  it.each([null, '2030-01-01T12:34:56', '2026-11-01T12:34:56'])(
    'saves a permanent, extended or shortened expiry (%s)',
    async (input) => {
      const expiresAt = input === null ? null : new Date(input).toISOString();
      const updated = { ...grant, expires_at: expiresAt };
      const { page, fixture } = await setup();
      updateExpiry.mockReturnValue(of(updated));
      grants.mockReturnValue(of([updated]));
      page.edit(grant);
      page.permanent = input === null;
      if (input) page.expiry = input;
      await page.saveExpiry('grant');
      fixture.detectChanges();
      expect(updateExpiry).toHaveBeenCalledWith('grant', expiresAt);
      expect(page.grants()).toEqual([updated]);
      expect(page.editing()).toBeNull();
      expect(page.saving()).toBeNull();
      if (input === null) expect(fixture.nativeElement.textContent).toContain('永久有效');
    },
  );

  it('round-trips UTC expiry through local datetime input and sends the local selection as UTC', async () => {
    const { page } = await setup();
    page.edit(grant);
    expect(new Date(page.expiry).toISOString()).toBe(new Date(grant.expires_at!).toISOString());
    const localDate = new Date(2028, 5, 20, 9, 15, 30);
    page.expiry = '2028-06-20T09:15:30';
    await page.saveExpiry('grant');
    expect(updateExpiry).toHaveBeenCalledWith('grant', localDate.toISOString());
  });

  it.each(['', 'not-a-date', '2028-02-30T12:00', '2026-01-01T00:00', '2026-10-16T00:00:00Z'])(
    'keeps an invalid or past datetime draft without sending it (%s)',
    async (input) => {
      const { page, fixture } = await setup();
      page.edit(grant);
      page.expiry = input;
      await page.saveExpiry('grant');
      fixture.detectChanges();
      expect(updateExpiry).not.toHaveBeenCalled();
      expect(page.editing()).toBe('grant');
      expect(page.expiry).toBe(input);
      expect(page.draftError()).toContain('有效且晚於');
      expect(fixture.nativeElement.querySelector('[role="alert"]')).not.toBeNull();
    },
  );

  it('retains the draft on save failure and allows another attempt', async () => {
    const { page } = await setup();
    updateExpiry.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 503 })));
    page.edit(grant);
    page.permanent = false;
    page.expiry = '2029-02-03T04:05';
    await page.saveExpiry('grant');
    expect(page.grants()).toEqual([grant]);
    expect(page.editing()).toBe('grant');
    expect(page.expiry).toBe('2029-02-03T04:05');
    expect(page.error()).toContain('暫時無法使用');
    expect(page.saving()).toBeNull();
    await page.saveExpiry('grant');
    expect(updateExpiry).toHaveBeenCalledTimes(2);
  });

  it('keeps a server-rejected future-time draft and gives a date-specific validation message', async () => {
    const { page } = await setup();
    updateExpiry.mockReturnValue(throwError(() => new HttpErrorResponse({ status: 422 })));
    page.edit(grant);
    page.expiry = '2029-03-04T05:06';
    await page.saveExpiry('grant');
    expect(page.editing()).toBe('grant');
    expect(page.expiry).toBe('2029-03-04T05:06');
    expect(page.draftError()).toContain('晚於目前時間');
    expect(page.error()).not.toContain('重新連線');
    expect(grants).toHaveBeenCalledOnce();
  });

  it.each([404, 409])(
    'reloads a grant after a %i expiry conflict without discarding the draft',
    async (status) => {
      const { page } = await setup();
      const revoked = { ...grant, revoked_at: '2026-10-16T00:00:00Z' };
      updateExpiry.mockReturnValue(throwError(() => new HttpErrorResponse({ status })));
      grants.mockReturnValue(of([revoked]));
      page.edit(grant);
      page.expiry = '2029-03-04T05:06';
      await page.saveExpiry('grant');
      expect(grants).toHaveBeenCalledTimes(2);
      expect(page.grants()).toEqual([revoked]);
      expect(page.editing()).toBe('grant');
      expect(page.expiry).toBe('2029-03-04T05:06');
      expect(page.error()).toContain('已過期或撤銷');
      await page.saveExpiry('grant');
      expect(updateExpiry).toHaveBeenCalledOnce();
    },
  );

  it('keeps the canonical saved grant visible when the following list refresh fails', async () => {
    const { page, fixture } = await setup();
    const updated = { ...grant, expires_at: null, last_used_at: '2026-10-15T12:00:00Z' };
    updateExpiry.mockReturnValue(of(updated));
    grants.mockReturnValue(throwError(() => new HttpErrorResponse({ status: 500 })));
    page.edit(grant);
    page.permanent = true;
    await page.saveExpiry('grant');
    fixture.detectChanges();
    expect(page.grants()).toEqual([updated]);
    expect(page.error()).toContain('失敗');
    expect(page.editing()).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('永久有效');
    expect(fixture.nativeElement.querySelector('ob-card')).not.toBeNull();
  });

  it.each(['before', 'during'])('fences an old list requested %s an expiry write', async (when) => {
    const { page } = await setup();
    const old = new Subject<ReviewOAuthGrant[]>();
    const response = new Subject<ReviewOAuthGrant>();
    const updated = { ...grant, expires_at: null };
    updateExpiry.mockReturnValue(response);
    page.edit(grant);
    page.permanent = true;
    grants.mockReturnValueOnce(old);
    let pendingList: Promise<void>;
    let pendingSave: Promise<void>;
    if (when === 'before') {
      pendingList = page.load();
      pendingSave = page.saveExpiry('grant');
    } else {
      pendingSave = page.saveExpiry('grant');
      pendingList = page.load();
    }
    grants.mockReturnValue(of([updated]));
    response.next(updated);
    await pendingSave;
    old.next([grant]);
    await pendingList;
    expect(page.grants()).toEqual([updated]);
  });

  it('does not allow expired or revoked grants to be edited or revived', async () => {
    const inactive = [
      { ...grant, id: 'expired', expires_at: '2000-01-01T00:00:00Z' },
      { ...grant, id: 'revoked', expires_at: null, revoked_at: '2026-10-15T00:00:00Z' },
    ];
    grants.mockReturnValue(of(inactive));
    const { page } = await setup();
    for (const item of inactive) {
      page.edit(item);
      page.permanent = true;
      await page.saveExpiry(item.id);
    }
    expect(page.editing()).toBeNull();
    expect(updateExpiry).not.toHaveBeenCalled();
  });

  it('rechecks expiry when submitting an already-open editor', async () => {
    const { page } = await setup();
    page.edit(grant);
    page.permanent = true;
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2027-01-02T00:00:00Z'));
    await page.saveExpiry('grant');
    expect(updateExpiry).not.toHaveBeenCalled();
  });

  it('prevents save, edit and revoke while saving and then allows revocation', async () => {
    const { page } = await setup();
    const response = new Subject<ReviewOAuthGrant>();
    updateExpiry.mockReturnValue(response);
    page.edit(grant);
    page.permanent = true;
    const pending = page.saveExpiry('grant');
    await page.saveExpiry('grant');
    await page.revoke('grant');
    page.cancelEdit();
    expect(page.editing()).toBe('grant');
    expect(updateExpiry).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
    const updated = { ...grant, expires_at: null };
    grants.mockReturnValue(of([updated]));
    response.next(updated);
    await pending;
    await page.revoke('grant');
    expect(revoke).toHaveBeenCalledOnce();
  });

  it('prevents saving during revocation and closes the revoked grant editor', async () => {
    const { page } = await setup();
    const response = new Subject<void>();
    revoke.mockReturnValue(response);
    page.edit(grant);
    page.permanent = true;
    const pending = page.revoke('grant');
    await page.saveExpiry('grant');
    expect(updateExpiry).not.toHaveBeenCalled();
    grants.mockReturnValue(of([{ ...grant, revoked_at: '2026-10-16T00:00:00Z' }]));
    response.next();
    await pending;
    expect(page.editing()).toBeNull();
  });

  it('isolates one successful expiry change from a later failed change on another grant', async () => {
    const other = { ...grant, id: 'other', client_name: 'Other' };
    grants.mockReturnValue(of([grant, other]));
    const { page } = await setup();
    const updated = { ...grant, expires_at: null };
    updateExpiry
      .mockReturnValueOnce(of(updated))
      .mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 409 })));
    grants.mockReturnValue(of([updated, other]));
    page.edit(grant);
    page.permanent = true;
    await page.saveExpiry('grant');
    page.edit(other);
    page.expiry = '2029-01-01T12:00';
    await page.saveExpiry('other');
    expect(page.grants()).toEqual([updated, other]);
    expect(page.editing()).toBe('other');
    expect(page.expiry).toBe('2029-01-01T12:00');
    expect(page.error()).toContain('無法更新有效期限');
  });

  it('ignores a save completion after leaving the page', async () => {
    const { page, fixture } = await setup();
    const response = new Subject<ReviewOAuthGrant>();
    updateExpiry.mockReturnValue(response);
    page.edit(grant);
    page.permanent = true;
    const pending = page.saveExpiry('grant');
    fixture.destroy();
    response.next({ ...grant, expires_at: null });
    await pending;
    expect(grants).toHaveBeenCalledOnce();
  });
});
