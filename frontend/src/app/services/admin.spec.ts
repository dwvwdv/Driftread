import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { AdminService } from './admin';
import { AdminKeyStore } from './admin-key';
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
        { provide: AdminKeyStore, useValue: { key: () => 'test-key' } },
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
});
