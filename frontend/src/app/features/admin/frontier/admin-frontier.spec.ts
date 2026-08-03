import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { AdminFrontier } from './admin-frontier';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { DiscoveryTarget, PaginatedDiscoveryTargets } from '../../../models';

const target = (id: string) =>
  ({
    id,
    url: `https://${id}.example.org`,
    host: `${id}.example.org`,
    status: 'pending',
  }) as DiscoveryTarget;

describe('AdminFrontier target paging', () => {
  it('ignores a list response overtaken by a newer query', () => {
    const responses: Subject<PaginatedDiscoveryTargets>[] = [];
    const admin = {
      listTargets: () => {
        const response = new Subject<PaginatedDiscoveryTargets>();
        responses.push(response);
        return response;
      },
      listSources: () => of([]),
    };

    TestBed.configureTestingModule({
      imports: [AdminFrontier],
      providers: [
        { provide: AdminService, useValue: admin },
        {
          provide: ToastService,
          useValue: { success: () => {}, warning: () => {} },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
      ],
    });

    const fixture = TestBed.createComponent(AdminFrontier);
    const frontier = fixture.componentInstance as unknown as {
      targets: () => DiscoveryTarget[];
      targetsTotal: () => number;
      loadingTargets: () => boolean;
      onPage: (page: number) => void;
    };
    fixture.detectChanges();

    frontier.onPage(2);
    expect(responses).toHaveLength(2);

    responses[1].next({ items: [target('new')], total: 21, page: 2, page_size: 20 });
    responses[0].next({ items: [target('stale')], total: 100, page: 1, page_size: 20 });

    expect(frontier.targets().map((item) => item.id)).toEqual(['new']);
    expect(frontier.targetsTotal()).toBe(21);
    expect(frontier.loadingTargets()).toBe(false);
  });
});
