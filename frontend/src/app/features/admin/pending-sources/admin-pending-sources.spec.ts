import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { AdminPendingSources } from './admin-pending-sources';
import { AdminService, ManagementPage } from '../../../services/admin';
import { DiscoveryTarget } from '../../../models';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

const target = (id: string) => ({ id, host: id + '.example.org' }) as DiscoveryTarget;
describe('AdminPendingSources', () => {
  function setup(admin: object) {
    TestBed.configureTestingModule({
      imports: [AdminPendingSources],
      providers: [
        {
          provide: AdminService,
          useValue: {
            discoveryControl: () =>
              of({ enabled: true, deployment_enabled: true, effective_enabled: true, version: 1 }),
            ...admin,
          },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
        { provide: ToastService, useValue: { success: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(AdminPendingSources);
    fixture.detectChanges();
    return fixture.componentInstance as unknown as {
      items: () => DiscoveryTarget[];
      selected: () => Set<string>;
      busy: () => boolean;
      toggle(id: string): void;
      go(page: number): void;
      remove(ids?: string[]): Promise<void>;
    };
  }
  it('does not let an old list restore removed targets', async () => {
    const old = new Subject<ManagementPage<DiscoveryTarget>>();
    const pendingTargets = vi
      .fn()
      .mockReturnValueOnce(of({ items: [target('a')], total: 51 }))
      .mockReturnValueOnce(old)
      .mockReturnValue(of({ items: [], total: 0 }));
    const removeTargets = vi.fn().mockReturnValue(of({ items: [{ id: 'a', ok: true }] }));
    const page = setup({ pendingTargets, removeTargets });
    page.go(2);
    old.next({ items: [target('a')], total: 51, limit: 50, offset: 50 });
    await page.remove(['a']);
    old.next({ items: [target('a')], total: 51, limit: 50, offset: 50 });
    expect(page.items()).toEqual([]);
    expect(removeTargets).toHaveBeenCalledWith(['a']);
  });
  it('keeps selection after a failed removal and permits retry', async () => {
    const page = setup({
      pendingTargets: () => of({ items: [target('a')], total: 1 }),
      removeTargets: () => throwError(() => new Error('offline')),
    });
    page.toggle('a');
    await page.remove();
    expect([...page.selected()]).toEqual(['a']);
    expect(page.busy()).toBe(false);
  });
});
