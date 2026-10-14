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
  beforeEach(() => {
    grants = vi.fn().mockReturnValue(of([grant]));
    revoke = vi.fn().mockReturnValue(of(undefined));
    TestBed.configureTestingModule({
      imports: [AdminMcpConnections],
      providers: [{ provide: ReviewOAuthService, useValue: { grants, revoke } }],
    });
  });
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
});
