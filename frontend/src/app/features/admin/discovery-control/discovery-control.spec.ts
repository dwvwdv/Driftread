import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { DiscoveryControlPanel } from './discovery-control';
import { AdminService, DiscoveryControl } from '../../../services/admin';
import { ToastService } from '../../../ui/toast/toast';

const state = (enabled: boolean, version = 1): DiscoveryControl => ({
  enabled,
  effective_enabled: enabled,
  deployment_enabled: true,
  version,
  updated_at: null,
});
describe('DiscoveryControlPanel', () => {
  function setup(admin: object) {
    TestBed.configureTestingModule({
      imports: [DiscoveryControlPanel],
      providers: [
        { provide: AdminService, useValue: admin },
        { provide: ToastService, useValue: { success: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(DiscoveryControlPanel);
    const page = fixture.componentInstance as unknown as {
      state: () => DiscoveryControl;
      busy: () => boolean;
      load(): void;
      toggle(): void;
    };
    fixture.detectChanges();
    return { fixture, page };
  }
  it('pauses with the observed version and waits for server confirmation', () => {
    const applied = new Subject<DiscoveryControl>();
    const setDiscoveryEnabled = vi.fn().mockReturnValue(applied);
    const { page } = setup({ discoveryControl: () => of(state(true, 7)), setDiscoveryEnabled });
    page.toggle();
    expect(setDiscoveryEnabled).toHaveBeenCalledWith(false, 7);
    expect(page.state().enabled).toBe(true);
    expect(page.busy()).toBe(true);
    applied.next(state(false, 8));
    expect(page.state().enabled).toBe(false);
    expect(page.busy()).toBe(false);
  });
  it('reloads after a conflict without optimistic state and can resume', () => {
    const applied = new Subject<DiscoveryControl>();
    const discoveryControl = vi
      .fn()
      .mockReturnValueOnce(of(state(true, 2)))
      .mockReturnValue(of(state(false, 3)));
    const setDiscoveryEnabled = vi
      .fn()
      .mockReturnValueOnce(applied)
      .mockReturnValue(of(state(true, 4)));
    const { page } = setup({ discoveryControl, setDiscoveryEnabled });
    page.toggle();
    applied.error(new Error('conflict'));
    expect(page.state()).toEqual(state(false, 3));
    page.toggle();
    expect(setDiscoveryEnabled).toHaveBeenLastCalledWith(true, 3);
    expect(page.state().effective_enabled).toBe(true);
  });
  it('discards an older GET after a newer read', () => {
    const old = new Subject<DiscoveryControl>();
    const discoveryControl = vi
      .fn()
      .mockReturnValueOnce(old)
      .mockReturnValue(of(state(false, 4)));
    const { page } = setup({ discoveryControl });
    page.load();
    old.next(state(true, 1));
    expect(page.state().enabled).toBe(false);
    expect(page.state().version).toBe(4);
  });
  it('disables enabling when the deployment ceiling is off', () => {
    const { fixture } = setup({
      discoveryControl: () => of({ ...state(false), deployment_enabled: false }),
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('部署尚未開放');
    expect(fixture.nativeElement.querySelector('button.ob-btn--primary').disabled).toBe(true);
  });
});
