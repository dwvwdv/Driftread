import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { AdminCandidates } from './admin-candidates';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { Feed, FeedCandidate, PaginatedFeedCandidates } from '../../../models';

const candidate = (i: number) =>
  ({
    id: `cand-${i}`,
    target_id: null,
    feed_url: `https://blog${i}.example.org/atom.xml`,
    title: `候選 ${i}`,
    website_url: null,
    source_host: `blog${i}.example.org`,
    referring_feed_count: 1,
    status: 'pending',
    feed_id: null,
    review_note: null,
    discovered_at: '2026-08-01T00:00:00Z',
    last_seen_at: null,
    reviewed_at: null,
  }) as FeedCandidate;

describe('AdminCandidates queue paging', () => {
  /** Requested (page, pageSize) pairs, so refetches can be asserted on. */
  let requests: [number, number][];
  let respondWith: PaginatedFeedCandidates;

  function setup(initial: PaginatedFeedCandidates, page = 1, pageSize = 20) {
    requests = [];
    respondWith = initial;

    const admin = {
      listCandidates: (p: number, size: number) => {
        requests.push([p, size]);
        return of(respondWith);
      },
      approveCandidate: () => of({ id: 'feed-1', title: '已入庫' } as Feed),
      rejectCandidate: () => of(candidate(99)),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AdminCandidates],
      providers: [
        provideRouter([]),
        { provide: AdminService, useValue: admin },
        {
          provide: ToastService,
          useValue: { success: () => {}, info: () => {}, warning: () => {}, danger: () => {} },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
      ],
    });

    const fixture = TestBed.createComponent(AdminCandidates);
    const reader = fixture.componentInstance as unknown as {
      candidates: () => FeedCandidate[];
      total: () => number;
      page: () => number;
      pageSize: () => number;
      approve: (c: FeedCandidate) => void;
      onPage: (p: number) => void;
    };
    fixture.detectChanges();
    Object.assign(fixture.componentInstance, { pageSize: () => pageSize });
    if (page !== 1) reader.onPage(page);
    return { fixture, reader };
  }

  it('does not refetch while rows remain on the page', () => {
    const { reader } = setup({ items: [1, 2, 3].map(candidate), total: 3, page: 1, page_size: 20 });
    const before = requests.length;

    reader.approve(candidate(1));

    expect(reader.candidates().length).toBe(2);
    expect(requests.length).toBe(before);
  });

  it('refetches when the last row on a page is cleared but the queue is not empty', () => {
    // One row rendered, 5 pending server-side: emptying it must not read as
    // "queue empty" — the empty branch also hides the paginator.
    const { reader } = setup({ items: [candidate(1)], total: 5, page: 1, page_size: 20 });
    respondWith = { items: [2, 3, 4, 5].map(candidate), total: 4, page: 1, page_size: 20 };
    const before = requests.length;

    reader.approve(candidate(1));

    expect(requests.length).toBe(before + 1);
    expect(reader.candidates().length).toBe(4);
  });

  it('shows the empty state without refetching once the queue really is empty', () => {
    const { reader } = setup({ items: [candidate(1)], total: 1, page: 1, page_size: 20 });
    const before = requests.length;

    reader.approve(candidate(1));

    expect(reader.candidates()).toEqual([]);
    expect(reader.total()).toBe(0);
    expect(requests.length).toBe(before);
  });

  it('clamps the page when the shrunken total makes the current one invalid', () => {
    // On page 3 of 3 at 20/page; clearing it leaves 21 pending, i.e. 2 pages.
    const { reader } = setup({ items: [candidate(1)], total: 41, page: 3, page_size: 20 }, 3);
    respondWith = { items: [candidate(2)], total: 40, page: 2, page_size: 20 };
    reader.approve(candidate(1));

    expect(reader.page()).toBe(2);
    expect(requests.at(-1)?.[0]).toBe(2);
  });
});
