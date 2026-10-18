import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { AdminCleanup } from './admin-cleanup';
import { AdminService, CleanupItem, ManagementPage } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

const item = (id: string) =>
  ({
    id,
    title: id,
    url: 'https://example.org/' + id,
    group_key: 'tech',
    group_size: 2,
  }) as CleanupItem;
describe('AdminCleanup', () => {
  function setup(admin: object, ask = vi.fn().mockResolvedValue(true)) {
    TestBed.configureTestingModule({
      imports: [AdminCleanup],
      providers: [
        { provide: AdminService, useValue: admin },
        { provide: ConfirmService, useValue: { ask } },
        { provide: ToastService, useValue: { success: vi.fn(), warning: vi.fn() } },
      ],
    });
    const fixture = TestBed.createComponent(AdminCleanup);
    const page = fixture.componentInstance as unknown as {
      items: () => CleanupItem[];
      selected: () => Set<string>;
      busy: () => boolean;
      toggle(id: string): void;
      reset(): void;
      apply(action: 'stage' | 'delete'): Promise<void>;
    };
    fixture.detectChanges();
    return { fixture, page, ask };
  }
  it('discards an old query response after filters change', () => {
    const responses: Subject<ManagementPage<CleanupItem>>[] = [];
    const { page } = setup({
      listCleanup: () => {
        const subject = new Subject<ManagementPage<CleanupItem>>();
        responses.push(subject);
        return subject;
      },
    });
    page.reset();
    responses[1].next({ items: [item('new')], total: 1, limit: 50, offset: 0 });
    responses[0].next({ items: [item('old')], total: 1, limit: 50, offset: 0 });
    expect(page.items()[0].id).toBe('new');
  });
  it('previews selected IDs and applies only after confirmation, preserving failed selection', async () => {
    const cleanupBatch = vi
      .fn()
      .mockReturnValueOnce(
        of({
          items: [
            { id: 'a', ok: true },
            { id: 'b', ok: true },
          ],
        }),
      )
      .mockReturnValueOnce(
        of({
          items: [
            { id: 'a', ok: true },
            { id: 'b', ok: false, error: 'not_found' },
          ],
        }),
      );
    const { page, ask } = setup({
      listCleanup: () => of({ items: [item('a'), item('b')], total: 2 }),
      cleanupBatch,
    });
    page.toggle('a');
    page.toggle('b');
    await page.apply('stage');
    expect(cleanupBatch.mock.calls).toEqual([
      ['feeds', ['a', 'b'], 'stage'],
      ['feeds', ['a', 'b'], 'stage', false],
    ]);
    expect(ask).toHaveBeenCalledOnce();
    expect([...page.selected()]).toEqual(['b']);
    expect(page.busy()).toBe(false);
  });
  it('does not apply when confirmation is cancelled', async () => {
    const cleanupBatch = vi.fn().mockReturnValue(of({ items: [{ id: 'a', ok: true }] }));
    const { page } = setup(
      { listCleanup: () => of({ items: [item('a')], total: 1 }), cleanupBatch },
      vi.fn().mockResolvedValue(false),
    );
    page.toggle('a');
    await page.apply('delete');
    expect(cleanupBatch).toHaveBeenCalledOnce();
    expect([...page.selected()]).toEqual(['a']);
  });
});
