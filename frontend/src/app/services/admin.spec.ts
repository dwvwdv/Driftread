import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { AdminService } from './admin';
import { ManageSessionService } from './manage-session';
import { ToastService } from '../ui/toast/toast';

/**
 * A 409 used to mean exactly one thing: approveCandidate() hitting an
 * already-rejected candidate. backend/errors.py now also maps
 * unique_violation (23505) to 409 globally, reachable from unrelated writes
 * like seedTargets() racing on discovery_targets.url — so report()'s 409
 * branch must not show the candidate-specific message for those.
 */
describe('AdminService 409 handling', () => {
  let httpMock: HttpTestingController;
  let toastCalls: { tone: string; text: string }[];

  beforeEach(() => {
    toastCalls = [];
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        AdminService,
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ManageSessionService,
          useValue: {
            headers: () => ({
              Authorization: 'Bearer user-token',
              'x-manage-session': 'otp-token',
            }),
            clear: () => {},
          },
        },
        { provide: Router, useValue: { navigate: () => Promise.resolve(true) } },
        {
          provide: ToastService,
          useValue: {
            info: (text: string) => toastCalls.push({ tone: 'info', text }),
            success: (text: string) => toastCalls.push({ tone: 'success', text }),
            warning: (text: string) => toastCalls.push({ tone: 'warning', text }),
            danger: (text: string) => toastCalls.push({ tone: 'danger', text }),
          },
        },
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('previews cleanup by default and requires the explicit apply call to send deletion confirmation', () => {
    const service = TestBed.inject(AdminService);
    service.cleanupBatch('articles', ['a'], 'delete').subscribe();
    const preview = httpMock.expectOne((req) => req.url.endsWith('/admin/cleanup/batch'));
    expect(preview.request.headers.get('x-manage-session')).toBe('otp-token');
    expect(preview.request.body).toEqual({
      kind: 'articles',
      ids: ['a'],
      action: 'delete',
      dry_run: true,
      confirm_delete: false,
    });
    preview.flush({ items: [], dry_run: true });
    service.cleanupBatch('articles', ['a'], 'delete', false).subscribe();
    const apply = httpMock.expectOne((req) => req.url.endsWith('/admin/cleanup/batch'));
    expect(apply.request.body.dry_run).toBe(false);
    expect(apply.request.body.confirm_delete).toBe(true);
    apply.flush({ items: [], dry_run: false });
  });

  it('previews the article cap unless dry_run=false and forwards a draft cap', () => {
    const service = TestBed.inject(AdminService);
    const summary = {
      dry_run: true,
      feeds_over_cap: 0,
      excess: 0,
      eligible: 0,
      deleted: 0,
      skipped_feeds: 0,
    };
    service.runArticleCap(true, 300).subscribe();
    const preview = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/retention/article-cap/run'),
    );
    expect(preview.request.method).toBe('POST');
    expect(preview.request.headers.get('x-manage-session')).toBe('otp-token');
    expect(preview.request.params.get('dry_run')).toBe('true');
    expect(preview.request.params.get('max_per_feed')).toBe('300');
    preview.flush(summary);
    service.runArticleCap(false).subscribe();
    const apply = httpMock.expectOne((req) => req.url.endsWith('/admin/retention/article-cap/run'));
    expect(apply.request.params.get('dry_run')).toBe('false');
    expect(apply.request.params.has('max_per_feed')).toBe(false);
    apply.flush({ ...summary, dry_run: false });
  });

  it('previews article expiry by default and explicitly forwards apply and draft days', () => {
    const service = TestBed.inject(AdminService);
    const summary = {
      dry_run: true,
      expired: 10,
      protected: 2,
      eligible: 8,
      deleted: 0,
      reads_affected: 3,
      skipped_feeds: 0,
      deletion_delay_seconds: 0,
    };
    service.runArticleTtl().subscribe();
    const preview = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/retention/article-ttl/run'),
    );
    expect(preview.request.method).toBe('POST');
    expect(preview.request.headers.get('x-manage-session')).toBe('otp-token');
    expect(preview.request.params.get('dry_run')).toBe('true');
    expect(preview.request.params.has('days')).toBe(false);
    expect(preview.request.params.has('limit')).toBe(false);
    preview.flush(summary);
    service.runArticleTtl(false, 14, 3).subscribe();
    const apply = httpMock.expectOne((req) => req.url.endsWith('/admin/retention/article-ttl/run'));
    expect(apply.request.params.get('dry_run')).toBe('false');
    expect(apply.request.params.get('days')).toBe('14');
    expect(apply.request.params.get('limit')).toBe('3');
    apply.flush({ ...summary, dry_run: false, eligible: 3, deleted: 3 });
  });

  it('uses bounded pagination and structured IDs for exploration management', () => {
    const service = TestBed.inject(AdminService);
    service.pendingTargets(50).subscribe();
    const listing = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/discovery/pending-targets'),
    );
    expect(listing.request.params.get('limit')).toBe('50');
    expect(listing.request.params.get('offset')).toBe('50');
    listing.flush({ items: [], total: 0 });
    service.removeTargets(['target-a']).subscribe();
    const remove = httpMock.expectOne((req) => req.url.endsWith('/admin/discovery/targets/remove'));
    expect(remove.request.body).toEqual({ ids: ['target-a'] });
    remove.flush({ items: [] });
  });

  it('shows the candidate-rejected message for approveCandidate', () => {
    const service = TestBed.inject(AdminService);

    // HttpTestingController's flush() delivers to the subscriber
    // synchronously, so no done()/async is needed — subscribing with a
    // no-op error handler is enough to keep RxJS from treating the
    // unhandled rejection as an uncaught error.
    service.approveCandidate('c1', { category: null, tags: [] }).subscribe({ error: () => {} });

    // A string passed to expectOne() is matched exactly against req.url, and
    // in this test environment HttpClient resolves the relative path against
    // http://localhost:8000 before the mock backend ever sees it — so match
    // by suffix instead of assuming the relative path survives untouched.
    httpMock
      .expectOne((req) => req.url.endsWith('/admin/discovery/candidates/c1/approve'))
      .flush(
        { detail: 'Candidate was rejected; re-approving must be done deliberately' },
        { status: 409, statusText: 'Conflict' },
      );

    expect(toastCalls).toEqual([{ tone: 'warning', text: '此候選先前已被拒絕，無法核准' }]);
  });

  it('shows a generic conflict message for a 409 from an unrelated call, not the candidate-rejected one', () => {
    const service = TestBed.inject(AdminService);

    service.seedTargets(['https://example.com/feed.xml']).subscribe({ error: () => {} });

    httpMock
      .expectOne((req) => req.url.endsWith('/admin/discovery/targets'))
      .flush({ detail: 'Resource already exists' }, { status: 409, statusText: 'Conflict' });

    expect(toastCalls).toEqual([
      { tone: 'danger', text: '加入待探測失敗：Resource already exists' },
    ]);
  });

  it.each(['hold', 'reject'] as const)(
    'explains an imported-candidate conflict for %s and preserves the error for the queue reload',
    (action) => {
      const service = TestBed.inject(AdminService);
      let caught: unknown;
      const response =
        action === 'hold'
          ? service.holdCandidate('c1', { note: null })
          : service.rejectCandidate('c1', { note: null, block_host: false });
      response.subscribe({
        error: (error) => {
          caught = error;
        },
      });
      httpMock
        .expectOne((req) => req.url.endsWith(`/admin/discovery/candidates/c1/${action}`))
        .flush(
          {
            detail: {
              code: 'candidate_already_imported',
              message: 'Candidate is already imported; manage it from feeds',
            },
          },
          { status: 409, statusText: 'Conflict' },
        );
      expect(toastCalls).toEqual([
        { tone: 'warning', text: '此來源已收錄，無法再保留或拒絕候選；請到「信息源管理」操作' },
      ]);
      expect(caught).toBeInstanceOf(HttpErrorResponse);
      expect((caught as HttpErrorResponse).status).toBe(409);
    },
  );

  it('explains a candidate version conflict without claiming it was imported', () => {
    const service = TestBed.inject(AdminService);
    service.holdCandidate('c1', { note: null }).subscribe({ error: () => {} });
    httpMock
      .expectOne((req) => req.url.endsWith('/admin/discovery/candidates/c1/hold'))
      .flush(
        {
          detail: {
            code: 'candidate_changed',
            message: 'Candidate changed; reload before retrying',
          },
        },
        { status: 409, statusText: 'Conflict' },
      );
    expect(toastCalls).toEqual([{ tone: 'warning', text: '候選狀態已更新，請確認最新清單後再試' }]);
  });

  it('fetches recent admissions with its limit and the verified management session', () => {
    const service = TestBed.inject(AdminService);
    let count = -1;
    service.recentAdmissions().subscribe(({ items }) => (count = items.length));
    const request = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/discovery/recent-admissions'),
    );
    expect(request.request.method).toBe('GET');
    expect(request.request.params.get('limit')).toBe('10');
    expect(request.request.headers.get('x-manage-session')).toBe('otp-token');
    expect(request.request.headers.get('Authorization')).toBe('Bearer user-token');
    request.flush({ items: [], limit: 10 });
    expect(count).toBe(0);
    service.recentAdmissions(5).subscribe();
    const custom = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/discovery/recent-admissions'),
    );
    expect(custom.request.params.get('limit')).toBe('5');
    custom.flush({ items: [], limit: 5 });
  });

  it('fetches read-only operation and storage snapshots with the verified management session', () => {
    const service = TestBed.inject(AdminService);
    service.operations().subscribe();
    service.storageStats().subscribe();
    service.storageHistory().subscribe();
    service.tableStorage().subscribe();
    service.ingestActivity().subscribe();
    for (const [path, param, value] of [
      ['/admin/operations', null, null],
      ['/admin/retention/stats', null, null],
      ['/admin/retention/history', 'days', '30'],
      ['/admin/retention/tables', 'days', '7'],
      ['/admin/operations/ingest', 'limit', '10'],
    ] as const) {
      const request = httpMock.expectOne((req) => req.url.endsWith(path));
      expect(request.request.method).toBe('GET');
      if (param) expect(request.request.params.get(param)).toBe(value);
      expect(request.request.headers.get('x-manage-session')).toBe('otp-token');
      expect(request.request.headers.get('Authorization')).toBe('Bearer user-token');
      expect(request.request.headers.has('x-api-key')).toBe(false);
      request.flush({});
    }
  });

  it('asks for the 24-hour ingest window by default and 7 days on request', () => {
    const service = TestBed.inject(AdminService);
    service.ingestActivity().subscribe();
    const day = httpMock.expectOne((req) => req.url.endsWith('/admin/operations/ingest'));
    expect(day.request.params.get('hours')).toBe('24');
    day.flush({});
    service.ingestActivity(5, 168).subscribe();
    const week = httpMock.expectOne((req) => req.url.endsWith('/admin/operations/ingest'));
    expect(week.request.params.get('hours')).toBe('168');
    expect(week.request.params.get('limit')).toBe('5');
    week.flush({});
  });

  it('reads settings and writes a typed setting with a version precondition', () => {
    const service = TestBed.inject(AdminService);
    service.globalSettings().subscribe();
    const read = httpMock.expectOne((req) => req.url.endsWith('/admin/settings'));
    expect(read.request.method).toBe('GET');
    expect(read.request.headers.get('x-manage-session')).toBe('otp-token');
    read.flush({ settings: [] });
    service.saveGlobalSetting('discovery.profiles', { profiles: [] }, 3).subscribe();
    const write = httpMock.expectOne((req) =>
      req.url.endsWith('/admin/settings/discovery.profiles'),
    );
    expect(write.request.method).toBe('PUT');
    expect(write.request.headers.get('x-manage-session')).toBe('otp-token');
    expect(write.request.body).toEqual({ value: { profiles: [] }, expected_version: 3 });
    write.flush({
      key: 'discovery.profiles',
      value: { profiles: [] },
      version: 4,
      updated_at: null,
    });
  });
});

describe('AdminService source review API', () => {
  let mock: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        AdminService,
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: ManageSessionService,
          useValue: {
            headers: () => ({ Authorization: 'Bearer user', 'x-manage-session': 'session' }),
          },
        },
        { provide: Router, useValue: {} },
        { provide: ToastService, useValue: {} },
      ],
    });
    mock = TestBed.inject(HttpTestingController);
  });
  afterEach(() => mock.verify());
  it('reads the latest review for a page with one authenticated batch request', () => {
    const service = TestBed.inject(AdminService);
    const ids = Array.from({ length: 20 }, (_, i) => `c${i}`);
    let result: unknown;
    service.latestSourceReviews(ids).subscribe((response) => (result = response));
    const read = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/latest'));
    expect(read.request.method).toBe('POST');
    expect(read.request.body).toEqual({ candidate_ids: ids });
    expect(read.request.headers.get('Authorization')).toBe('Bearer user');
    expect(read.request.headers.get('x-manage-session')).toBe('session');
    expect(read.request.headers.has('x-api-key')).toBe(false);
    const response = { items: [{ id: 'r1', candidate_id: 'c3', status: 'ready' }] };
    read.flush(response);
    expect(result).toEqual(response);
  });
  it('sends the awaiting-human candidate filter to the server', () => {
    TestBed.inject(AdminService).listCandidates(2, 20, 'awaiting_human').subscribe();
    const request = mock.expectOne((r) => r.url.endsWith('/admin/discovery/candidates'));
    expect(request.request.params.get('status')).toBe('awaiting_human');
    expect(request.request.params.get('page')).toBe('2');
    request.flush({ items: [], total: 0 });
  });

  it('uses authenticated batch endpoints and preserves per-item failures', () => {
    const service = TestBed.inject(AdminService);
    let result: unknown;
    service
      .batchRequestSourceReviews(['c1', 'c2'], true, { c1: 'r1', c2: 'r2' })
      .subscribe((response) => (result = response));
    const request = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/batch/request'));
    expect(request.request.body).toEqual({
      candidate_ids: ['c1', 'c2'],
      retry: true,
      expected_review_ids: { c1: 'r1', c2: 'r2' },
    });
    expect(request.request.headers.get('x-manage-session')).toBe('session');
    const partial = {
      items: [{ candidate_id: 'c2', ok: false, status_code: 409, detail: {} }],
      succeeded: 1,
      failed: 1,
    };
    request.flush(partial);
    expect(result).toEqual(partial);
    service.batchResolveSourceReviews(['r1'], 'defer').subscribe();
    const resolve = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/batch/resolve'));
    expect(resolve.request.body).toEqual({ review_ids: ['r1'], decision: 'defer' });
    expect(resolve.request.headers.get('Authorization')).toBe('Bearer user');
    resolve.flush({ items: [], succeeded: 0, failed: 0 });
  });

  it('omits expected review IDs for a fresh batch request', () => {
    TestBed.inject(AdminService).batchRequestSourceReviews(['c1']).subscribe();
    const request = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/batch/request'));
    expect(request.request.body).toEqual({ candidate_ids: ['c1'], retry: false });
    request.flush({ items: [], succeeded: 0, failed: 0 });
  });

  it('filters reviews by candidate and submits request, retry and human resolution with management authentication', () => {
    const service = TestBed.inject(AdminService);
    service.listSourceReviews('c1').subscribe();
    const read = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews'));
    expect(read.request.params.get('candidate_id')).toBe('c1');
    expect(read.request.params.get('limit')).toBe('1');
    expect(read.request.headers.get('x-manage-session')).toBe('session');
    read.flush({ items: [] });
    service.requestSourceReview('c1').subscribe();
    service.retrySourceReview('r1').subscribe();
    service.resolveSourceReview('r1', 'defer').subscribe();
    for (const [path, body] of [
      ['/candidates/c1/request', {}],
      ['/r1/retry', {}],
      ['/r1/resolve', { decision: 'defer' }],
    ] as const) {
      const write = mock.expectOne((r) => r.url.endsWith(`/admin/source-reviews${path}`));
      expect(write.request.method).toBe('POST');
      expect(write.request.body).toEqual(body);
      expect(write.request.headers.get('Authorization')).toBe('Bearer user');
      expect(write.request.headers.has('x-api-key')).toBe(false);
      write.flush({});
    }
  });
  it.each(['agree', 'disagree'] as const)(
    'sends %s judgment independently from the resulting source decision',
    (assessment) => {
      const service = TestBed.inject(AdminService);
      service.judgeSourceReview('r1', assessment).subscribe();
      const single = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/r1/judgment'));
      expect(single.request.body).toEqual({ assessment });
      expect(single.request.headers.get('x-manage-session')).toBe('session');
      single.flush({});
      service.judgeSourceReviews(['r1', 'r2'], assessment).subscribe();
      const batch = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/batch/judgment'));
      expect(batch.request.body).toEqual({ review_ids: ['r1', 'r2'], assessment });
      batch.flush({ items: [], succeeded: 0, failed: 0 });
    },
  );
  it('sends an explicit source decision only for a human disagreement with abstention', () => {
    TestBed.inject(AdminService).judgeSourceReview('r1', 'disagree', 'accept').subscribe();
    const request = mock.expectOne((r) => r.url.endsWith('/admin/source-reviews/r1/judgment'));
    expect(request.request.body).toEqual({ assessment: 'disagree', decision: 'accept' });
    request.flush({});
  });
});
