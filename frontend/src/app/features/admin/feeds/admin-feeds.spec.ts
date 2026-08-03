import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { AdminFeeds } from './admin-feeds';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { Feed, PaginatedFeeds } from '../../../models';

const feed = (i: number) =>
  ({
    id: `feed-${i}`,
    title: `信息源 ${i}`,
    url: `https://blog${i}.example.org/atom.xml`,
    description: null,
    category: null,
    tags: [],
    language: null,
    website_url: null,
    archived: false,
  }) as unknown as Feed;

/**
 * The active tab pages with .range(offset, …) over rows filtered by `archived`,
 * so archiving one feed shifts every later feed down an index. These cover the
 * consequences of that — the same shape as the candidate queue, which is why the
 * fix lives in both.
 */
describe('AdminFeeds active paging', () => {
  /** Requested page numbers, so refetches can be asserted on. */
  let requests: number[];
  let respondWith: PaginatedFeeds;
  /** When set, list responses are held open here instead of resolving inline. */
  let held: Subject<PaginatedFeeds>[] | null;

  function setup(initial: PaginatedFeeds, pageSize = 50) {
    requests = [];
    respondWith = initial;
    held = null;

    const admin = {
      listFeeds: (page: number) => {
        requests.push(page);
        if (held) {
          const response = new Subject<PaginatedFeeds>();
          held.push(response);
          return response;
        }
        return of(respondWith);
      },
      listArchived: () => of([] as Feed[]),
      listUnhealthy: () => of([]),
      archive: () => of(undefined),
      unarchive: () => of(undefined),
      refreshFeed: () => of({ new_articles: 0, inserted: 0 }),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AdminFeeds],
      providers: [
        { provide: AdminService, useValue: admin },
        {
          provide: ToastService,
          useValue: { success: () => {}, info: () => {}, warning: () => {}, danger: () => {} },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
      ],
    });

    const fixture = TestBed.createComponent(AdminFeeds);
    const reader = fixture.componentInstance as unknown as {
      active: () => Feed[];
      activeTotal: () => number;
      page: () => number;
      loading: () => boolean;
      archive: (f: Feed) => Promise<void>;
    };
    fixture.detectChanges();
    Object.assign(fixture.componentInstance, { pageSize: () => pageSize });
    return { fixture, reader };
  }

  it('backfills the page after an archive even while rows remain', async () => {
    const { reader } = setup({ items: [1, 2, 3].map(feed), total: 60, page: 1, page_size: 50 });
    respondWith = { items: [2, 3, 4].map(feed), total: 59, page: 1, page_size: 50 };
    const before = requests.length;

    await reader.archive(feed(1));

    expect(requests.length).toBe(before + 1);
    expect(reader.active().map((f) => f.id)).toEqual(['feed-2', 'feed-3', 'feed-4']);
  });

  it('backfills quietly, without swapping the list for the spinner', async () => {
    const { reader } = setup({ items: [1, 2, 3].map(feed), total: 60, page: 1, page_size: 50 });
    held = [];

    await reader.archive(feed(1));

    expect(held.length).toBe(1);
    expect(reader.loading()).toBe(false);
    expect(reader.active().map((f) => f.id)).toEqual(['feed-2', 'feed-3']);
  });

  it('ignores a backfill that is overtaken by a later one', async () => {
    const { reader } = setup({ items: [1, 2, 3].map(feed), total: 60, page: 1, page_size: 50 });
    held = [];

    await reader.archive(feed(1));
    await reader.archive(feed(2));
    expect(held.length).toBe(2);

    const [stale, latest] = held;
    latest.next({ items: [3, 4].map(feed), total: 58, page: 1, page_size: 50 });
    stale.next({ items: [2, 3, 4].map(feed), total: 59, page: 1, page_size: 50 });

    expect(reader.active().map((f) => f.id)).toEqual(['feed-3', 'feed-4']);
    expect(reader.activeTotal()).toBe(58);
  });

  it('does not refetch once the last active feed is archived', async () => {
    const { reader } = setup({ items: [feed(1)], total: 1, page: 1, page_size: 50 });
    const before = requests.length;

    await reader.archive(feed(1));

    expect(reader.active()).toEqual([]);
    expect(reader.activeTotal()).toBe(0);
    expect(requests.length).toBe(before);
  });
});
