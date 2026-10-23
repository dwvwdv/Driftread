import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
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
  let sourceFailure: boolean;
  let sourceCalls: { id: string; body: unknown }[];
  let asks = 0;
  let confirmAnswer = true;

  function setup(initial: PaginatedFeeds, pageSize = 50) {
    requests = [];
    respondWith = initial;
    held = null;
    sourceFailure = false;
    sourceCalls = [];
    asks = 0;
    confirmAnswer = true;

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
      updateSourceMetadata: (id: string, body: unknown) => {
        sourceCalls.push({ id, body });
        return sourceFailure ? throwError(() => new Error('unavailable')) : of(feed(1));
      },
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
        {
          provide: ConfirmService,
          useValue: { ask: () => (asks++, Promise.resolve(confirmAnswer)) },
        },
      ],
    });

    const fixture = TestBed.createComponent(AdminFeeds);
    const reader = fixture.componentInstance as unknown as {
      active: () => Feed[];
      activeTotal: () => number;
      page: () => number;
      loading: () => boolean;
      archive: (f: Feed) => Promise<void>;
      editSource: (f: Feed) => void;
      saveSource: () => Promise<void>;
      editing: () => Feed | null;
      savingSource: () => boolean;
      fetchStatus: (feed: Feed) => string;
      sourceRole: string;
      signalGroup: string;
      articleCap: number | null;
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

  it('retains unsaved source edits after failure and refreshes inventory after retry', () => {
    const { reader } = setup({ items: [feed(1)], total: 1, page: 1, page_size: 50 });
    reader.editSource(feed(1));
    reader.sourceRole = 'signal_only';
    reader.signalGroup = ' shared-source ';
    sourceFailure = true;
    reader.saveSource();
    expect(reader.editing()?.id).toBe('feed-1');
    expect(reader.savingSource()).toBe(false);
    sourceFailure = false;
    reader.saveSource();
    expect(sourceCalls.at(-1)).toEqual({ id: 'feed-1', body: {
      participation_mode: 'signal_only', fulltext_policy: 'rss', signal_group: 'shared-source', first_party: false,
      article_cap: null,
    } });
    expect(reader.editing()).toBeNull();
    expect(requests).toEqual([1, 1]);
  });

  it('sends a per-source article cap and rejects one outside 100–100000', async () => {
    const { reader } = setup({ items: [feed(1)], total: 1, page: 1, page_size: 50 });
    reader.editSource({ ...feed(1), article_cap: 500 });
    expect(reader.articleCap).toBe(500);
    reader.articleCap = 99;
    await reader.saveSource();
    expect(sourceCalls).toEqual([]);
    expect(reader.editing()?.id).toBe('feed-1');
    reader.articleCap = 300;
    await reader.saveSource();
    expect(asks).toBe(1);
    expect((sourceCalls.at(-1)?.body as { article_cap: number }).article_cap).toBe(300);
    reader.editSource({ ...feed(1), article_cap: 300 });
    reader.articleCap = 5000;
    await reader.saveSource();
    expect(asks).toBe(1);
    expect((sourceCalls.at(-1)?.body as { article_cap: number }).article_cap).toBe(5000);
  });

  it('confirms before clearing an override, which may fall back to a lower global cap', async () => {
    const { reader } = setup({ items: [feed(1)], total: 1, page: 1, page_size: 50 });
    reader.editSource({ ...feed(1), article_cap: 5000 });
    reader.articleCap = null;
    confirmAnswer = false;
    await reader.saveSource();
    expect(asks).toBe(1);
    expect(sourceCalls).toEqual([]);
    confirmAnswer = true;
    await reader.saveSource();
    expect(asks).toBe(2);
    expect((sourceCalls.at(-1)?.body as { article_cap: null }).article_cap).toBeNull();
  });

  it('does not lower a per-source cap when the confirmation is declined', async () => {
    const { reader } = setup({ items: [feed(1)], total: 1, page: 1, page_size: 50 });
    confirmAnswer = false;
    reader.editSource(feed(1));
    reader.articleCap = 200;
    await reader.saveSource();
    expect(asks).toBe(1);
    expect(sourceCalls).toEqual([]);
    expect(reader.editing()?.id).toBe('feed-1');
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

  it('distinguishes an excluded source, an empty feed and an unfinished first fetch', () => {
    const excluded = { ...feed(1), article_count: 0, archived_at: '2026-10-16T00:00:00Z',
      last_failure_reason: 'Excluded before admission: duplicates existing source mirror' };
    const { reader, fixture } = setup({ items: [excluded], total: 1, page: 1, page_size: 50 });
    expect(fixture.nativeElement.textContent).toContain('未准入：重複來源');
    expect(reader.fetchStatus({ ...feed(2), article_count: 0, last_fetch_result: {
      outcome: 'empty_feed', at: '2026-10-16T00:00:00Z', entries_seen: 0,
    } })).toBe('來源回傳零篇文章');
    expect(reader.fetchStatus({ ...feed(3), article_count: 0, last_fetched_at: null })).toBe('等待首次抓取');
    expect(reader.fetchStatus({ ...feed(4), article_count: 0, last_fetched_at: '2026-10-01T00:00:00Z' })).toContain('歷史原因未記錄');
    expect(reader.fetchStatus({ ...excluded, archived_at: null, admitted_at: '2026-10-16T01:00:00Z',
      last_fetch_result: { outcome: 'excluded', at: '2026-10-16T00:00:00Z' },
    })).toBe('已人工核准，等待重新抓取');
  });
});
