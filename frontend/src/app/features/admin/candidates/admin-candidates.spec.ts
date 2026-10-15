import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { AdminCandidates } from './admin-candidates';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { Feed, FeedCandidate, PaginatedFeedCandidates, SourceReview } from '../../../models';

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
  /** When set, list responses are held open here instead of resolving inline. */
  let held: Subject<PaginatedFeedCandidates>[] | null;

  function setup(initial: PaginatedFeedCandidates, page = 1, pageSize = 20) {
    requests = [];
    respondWith = initial;
    held = null;

    const admin = {
      latestSourceReviews: () => of({ items: [] }),
      listCandidates: (p: number, size: number) => {
        requests.push([p, size]);
        if (held) {
          const response = new Subject<PaginatedFeedCandidates>();
          held.push(response);
          return response;
        }
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
      loading: () => boolean;
      approve: (c: FeedCandidate) => void;
      onPage: (p: number) => void;
    };
    fixture.detectChanges();
    Object.assign(fixture.componentInstance, { pageSize: () => pageSize });
    if (page !== 1) reader.onPage(page);
    return { fixture, reader };
  }

  it('backfills from the server even while rows remain on the page', () => {
    // The queue is paginated with .range(offset, …) over status='pending', so
    // approving one row shifts every later row down an index. Keeping the short
    // page would leave the first candidate of page 2 sitting in a slot we are no
    // longer showing, and pressing Next would step straight over it.
    const { reader } = setup({
      items: [1, 2, 3].map(candidate),
      total: 25,
      page: 1,
      page_size: 20,
    });
    // What page 1 looks like once cand-1 is out: the shifted row joins the end.
    respondWith = { items: [2, 3, 4].map(candidate), total: 24, page: 1, page_size: 20 };
    const before = requests.length;

    reader.approve(candidate(1));

    expect(requests.length).toBe(before + 1);
    expect(reader.candidates().map((c) => c.id)).toEqual(['cand-2', 'cand-3', 'cand-4']);
  });

  it('backfills quietly, without swapping the list for the spinner', () => {
    // A teardown between every two decisions would take the row the operator is
    // reading off the screen mid-review.
    const { reader } = setup({
      items: [1, 2, 3].map(candidate),
      total: 25,
      page: 1,
      page_size: 20,
    });
    held = [];

    reader.approve(candidate(1));

    expect(held.length).toBe(1);
    expect(reader.loading()).toBe(false);
    expect(reader.candidates().map((c) => c.id)).toEqual(['cand-2', 'cand-3']);
  });

  it('ignores a backfill that is overtaken by a later one', () => {
    // Two quick approvals leave two backfills in flight. The first observed the
    // queue before the second approval landed, so applying it last would put the
    // already-approved row back on screen.
    const { reader } = setup({
      items: [1, 2, 3].map(candidate),
      total: 25,
      page: 1,
      page_size: 20,
    });
    held = [];

    reader.approve(candidate(1));
    reader.approve(candidate(2));
    expect(held.length).toBe(2);

    const [stale, latest] = held;
    latest.next({ items: [3, 4].map(candidate), total: 23, page: 1, page_size: 20 });
    stale.next({ items: [2, 3, 4].map(candidate), total: 24, page: 1, page_size: 20 });

    expect(reader.candidates().map((c) => c.id)).toEqual(['cand-3', 'cand-4']);
    expect(reader.total()).toBe(23);
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

describe('AdminCandidates source review', () => {
  const task = (status: SourceReview['status'] = 'ready'): SourceReview => ({
    id: 'r1',
    candidate_id: 'cand-1',
    status,
    engine: 'external_agent',
  });
  function setup(overrides: Record<string, unknown> = {}) {
    const calls = { request: 0, retry: 0, resolve: [] as string[], read: 0 };
    const admin = {
      listCandidates: () => of({ items: [candidate(1)], total: 1, page: 1, page_size: 20 }),
      latestSourceReviews: () => {
        calls.read++;
        return of({ items: [] });
      },
      requestSourceReview: () => {
        calls.request++;
        return of(task('requested'));
      },
      retrySourceReview: () => {
        calls.retry++;
        return of(task('requested'));
      },
      resolveSourceReview: (_id: string, decision: string) => {
        calls.resolve.push(decision);
        return of(task('deferred'));
      },
      ...overrides,
    };
    TestBed.configureTestingModule({
      imports: [AdminCandidates],
      providers: [
        { provide: AdminService, useValue: admin },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
        { provide: ToastService, useValue: { info: () => {}, warning: () => {} } },
      ],
    });
    const fixture = TestBed.createComponent(AdminCandidates);
    fixture.detectChanges();
    const component = fixture.componentInstance as unknown as {
      sourceReviews: {
        (): Record<string, SourceReview | null>;
        set: (v: Record<string, SourceReview | null>) => void;
      };
      requestReview: (c: FeedCandidate, retry?: boolean) => void;
      resolveReview: (c: FeedCandidate, decision: 'accept' | 'reject' | 'defer') => void;
      load: (quiet?: boolean) => void;
      onPage: (page: number) => void;
      reviewErrors: () => Set<string>;
    };
    return { component, fixture, calls };
  }

  it.each([20, 50])('reads %i candidates in one batch and maps missing reviews to null', (size) => {
    const ids: string[][] = [];
    const { component } = setup({
      listCandidates: () =>
        of({ items: Array.from({ length: size }, (_, i) => candidate(i + 1)), total: size }),
      latestSourceReviews: (candidateIds: string[]) => {
        ids.push(candidateIds);
        return of({ items: [{ ...task(), candidate_id: 'cand-2' }] });
      },
      listSourceReviews: () => {
        throw new Error('Per-candidate review reads must not be used');
      },
    });
    expect(ids).toEqual([Array.from({ length: size }, (_, i) => `cand-${i + 1}`)]);
    expect(component.sourceReviews()['cand-2']?.id).toBe('r1');
    expect(component.sourceReviews()['cand-1']).toBeNull();
    expect(Object.keys(component.sourceReviews())).toHaveLength(size);
  });

  it('does not request reviews for an empty candidate page', () => {
    const { calls } = setup({ listCandidates: () => of({ items: [], total: 0 }) });
    expect(calls.read).toBe(0);
  });

  it('marks every candidate after a batch failure and clears errors after a successful reload', () => {
    const response = new Subject<{ items: SourceReview[] }>();
    const { component, fixture, calls } = setup({
      listCandidates: () => of({ items: [candidate(1), candidate(2)], total: 2 }),
      latestSourceReviews: () => response,
    });
    response.error(new HttpErrorResponse({ status: 503 }));
    fixture.detectChanges();
    expect([...component.reviewErrors()]).toEqual(['cand-1', 'cand-2']);
    expect(fixture.nativeElement.textContent.match(/無法讀取評審狀態/g)).toHaveLength(2);
    component.requestReview(candidate(1));
    component.requestReview(candidate(2));
    expect(calls.request).toBe(0);
    TestBed.inject(AdminService).latestSourceReviews = () => of({ items: [] });
    component.load();
    expect(component.reviewErrors().size).toBe(0);
    expect(component.sourceReviews()).toEqual({ 'cand-1': null, 'cand-2': null });
  });

  it.each([false, true])(
    'cancels the previous batch immediately when reloading (quiet=%s)',
    (quiet) => {
      const old = new Subject<{ items: SourceReview[] }>();
      const latest = new Subject<{ items: SourceReview[] }>();
      let reads = 0;
      const { component } = setup({ latestSourceReviews: () => (++reads === 1 ? old : latest) });
      const list = new Subject<PaginatedFeedCandidates>();
      TestBed.inject(AdminService).listCandidates = () => list;
      expect(old.observed).toBe(true);
      component.load(quiet);
      expect(old.observed).toBe(false);
      expect(latest.observed).toBe(false);
      list.next({ items: [candidate(1)], total: 1, page: 1, page_size: 20 });
      latest.next({ items: [task('requested')] });
      old.next({ items: [] });
      expect(component.sourceReviews()['cand-1']?.status).toBe('requested');
    },
  );

  it('cancels the previous page batch and ignores its late response', () => {
    const old = new Subject<{ items: SourceReview[] }>();
    const latest = new Subject<{ items: SourceReview[] }>();
    let reads = 0;
    const { component } = setup({
      listCandidates: (page: number) => of({ items: [candidate(page)], total: 40 }),
      latestSourceReviews: () => (++reads === 1 ? old : latest),
    });
    component.onPage(2);
    expect(old.observed).toBe(false);
    latest.next({ items: [{ ...task(), candidate_id: 'cand-2' }] });
    old.next({ items: [task('failed')] });
    expect(component.sourceReviews()['cand-1']).toBeUndefined();
    expect(component.sourceReviews()['cand-2']?.status).toBe('ready');
  });

  it('cancels an old batch when an action starts and excludes busy rows from later reads', () => {
    const old = new Subject<{ items: SourceReview[] }>();
    const write = new Subject<SourceReview>();
    const ids: string[][] = [];
    const { component } = setup({
      listCandidates: () => of({ items: [candidate(1), candidate(2)], total: 2 }),
      latestSourceReviews: (candidateIds: string[]) => {
        ids.push(candidateIds);
        return ids.length === 1 ? old : of({ items: ids.length === 2 ? [] : [task('requested')] });
      },
      requestSourceReview: () => write,
    });
    component.sourceReviews.set({ 'cand-1': null });
    component.requestReview(candidate(1));
    expect(old.observed).toBe(false);
    component.load(true);
    expect(ids[1]).toEqual(['cand-2']);
    write.next(task('requested'));
    expect(ids[2]).toEqual(['cand-1', 'cand-2']);
    old.next({ items: [task('failed')] });
    expect(component.sourceReviews()['cand-1']?.status).toBe('requested');
  });

  it('requests one task, guards double clicks, and refreshes using reads only', () => {
    const response = new Subject<SourceReview>();
    let requests = 0;
    const { component, calls } = setup({
      requestSourceReview: () => {
        requests++;
        return response;
      },
      latestSourceReviews: () => of({ items: [task('requested')] }),
    });
    // Start from a known empty task list, as returned by the first review read.
    component.sourceReviews.set({ 'cand-1': null });
    component.requestReview(candidate(1));
    component.requestReview(candidate(1));
    expect(requests).toBe(1);
    response.next(task('requested'));
    expect(component.sourceReviews()['cand-1']?.status).toBe('requested');
    expect(requests).toBe(1);
  });

  it('shows structured results and permits explicit retry only for recoverable tasks', () => {
    const { component, fixture, calls } = setup({
      latestSourceReviews: () =>
        of({
          items: [
            {
              ...task('failed'),
              result: {
                decision: 'abstain',
                confidence: 0.5,
                dimensions: { quality: 0.4, spam_probability: 0.1, relevance: 0.8 },
                reason_codes: ['insufficient_sample'],
              },
            },
          ],
        }),
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('服務失敗');
    expect(fixture.nativeElement.textContent).toContain('無法判定');
    expect(fixture.nativeElement.textContent).toContain('insufficient_sample');
    component.requestReview(candidate(1), true);
    expect(calls.retry).toBe(1);
    component.sourceReviews.set({ 'cand-1': task('leased') });
    component.requestReview(candidate(1), true);
    expect(calls.retry).toBe(1);
  });

  it('resolves human review through its dedicated endpoint and protects manual overrides', () => {
    const { component, calls } = setup({
      latestSourceReviews: () => of({ items: [task('awaiting_human')] }),
    });
    component.resolveReview(candidate(1), 'defer');
    expect(calls.resolve).toEqual(['defer']);
    component.sourceReviews.set({ 'cand-1': task('failed') });
    component.requestReview({ ...candidate(1), manual_override: 'hold' }, true);
    expect(calls.retry).toBe(0);
  });

  it('ignores old review reads that finish after a submitted request', () => {
    const old = new Subject<{ items: SourceReview[] }>();
    let readCount = 0;
    const { component } = setup({
      latestSourceReviews: () => (++readCount === 1 ? old : of({ items: [task('requested')] })),
    });
    component.sourceReviews.set({ 'cand-1': null });
    component.requestReview(candidate(1));
    old.next({ items: [] });
    expect(component.sourceReviews()['cand-1']?.status).toBe('requested');
  });

  it('does not offer a fresh task after a failed state read and cancels reads on leaving', () => {
    const response = new Subject<{ items: SourceReview[] }>();
    const { component, fixture } = setup({ latestSourceReviews: () => response });
    response.error(new Error('offline'));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('無法讀取評審狀態');
    expect(fixture.nativeElement.textContent).not.toContain('請求 AI 評審');
    const pending = new Subject<{ items: SourceReview[] }>();
    TestBed.inject(AdminService).latestSourceReviews = () => pending;
    component.load();
    fixture.destroy();
    pending.next({ items: [task()] });
    expect(component.sourceReviews()['cand-1']).toBeUndefined();
  });
  it.each([
    ['provider_key_missing', '尚未設定 Jev 金鑰'],
    ['budget_exceeded', '模型預算已達上限'],
    ['provider_outcome_unknown', '請求逾時或中斷'],
    ['request_unknown_paused', '已暫停'],
    ['provider_http_error', '模型服務未成功回應'],
    ['sample_fetch_failed', '無法取得來源樣本'],
    ['provider_model_unsupported', '模型版本尚未支援'],
  ])('shows an actionable safe explanation for %s', (code, message) => {
    const { fixture } = setup({
      latestSourceReviews: () => of({ items: [{ ...task('failed'), error_code: code }] }),
    });
    expect(fixture.nativeElement.textContent).toContain(message);
    expect(fixture.nativeElement.textContent).not.toContain(code);
  });

  it('keeps unknown provider error text out of the UI', () => {
    const { fixture } = setup({
      latestSourceReviews: () =>
        of({ items: [{ ...task('failed'), error_code: 'raw-secret-provider-error' }] }),
    });
    expect(fixture.nativeElement.textContent).toContain('請檢查服務狀態後重試');
    expect(fixture.nativeElement.textContent).not.toContain('raw-secret-provider-error');
  });

  it('explains deferred review even without an operational error code', () => {
    const { fixture } = setup({ latestSourceReviews: () => of({ items: [task('deferred')] }) });
    expect(fixture.nativeElement.textContent).toContain('樣本或評審結果未達收錄門檻');
  });
  it('allows retrying a model rejection after policy changes while protecting manual rejection', () => {
    const { component, fixture, calls } = setup({
      latestSourceReviews: () => of({ items: [task('rejected')] }),
    });
    expect(fixture.nativeElement.textContent).toContain('重試 AI 評審');
    component.requestReview(candidate(1), true);
    expect(calls.retry).toBe(1);
    component.sourceReviews.set({ 'cand-1': { ...task('rejected'), manual_override: 'reject' } });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).not.toContain('重試 AI 評審');
    component.requestReview(candidate(1), true);
    expect(calls.retry).toBe(1);
  });
});

describe('AdminCandidates manual decision conflicts', () => {
  it.each(['hold', 'reject'] as const)(
    'reloads an already-imported candidate after %s conflict without reporting success',
    async (action) => {
      let reads = 0;
      const notices: string[] = [];
      const conflict = new HttpErrorResponse({ status: 409 });
      TestBed.configureTestingModule({
        imports: [AdminCandidates],
        providers: [
          {
            provide: AdminService,
            useValue: {
              listCandidates: () =>
                of({
                  items: ++reads === 1 ? [candidate(1)] : [],
                  total: reads === 1 ? 1 : 0,
                  page: 1,
                  page_size: 20,
                }),
              latestSourceReviews: () => of({ items: [] }),
              holdCandidate: () => throwError(() => conflict),
              rejectCandidate: () => throwError(() => conflict),
            },
          },
          {
            provide: ToastService,
            useValue: {
              info: (text: string) => notices.push(text),
              success: (text: string) => notices.push(text),
            },
          },
          { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
        ],
      });
      const fixture = TestBed.createComponent(AdminCandidates);
      fixture.detectChanges();
      const component = fixture.componentInstance as unknown as {
        hold: (c: FeedCandidate) => void;
        reject: (c: FeedCandidate) => Promise<void>;
        candidates: () => FeedCandidate[];
      };
      await component[action](candidate(1));
      expect(reads).toBe(2);
      expect(component.candidates()).toEqual([]);
      expect(notices).toEqual([]);
    },
  );
});
