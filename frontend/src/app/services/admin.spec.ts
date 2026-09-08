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

  it('shows the candidate-rejected message for approveCandidate', (done) => {
    const service = TestBed.inject(AdminService);

    service.approveCandidate('c1', { category: null, tags: [] }).subscribe({
      error: () => {
        expect(toastCalls).toEqual([{ tone: 'warning', text: '此候選先前已被拒絕，無法核准' }]);
        done();
      },
    });

    httpMock
      .expectOne('/api/admin/discovery/candidates/c1/approve')
      .flush(
        { detail: 'Candidate was rejected; re-approving must be done deliberately' },
        { status: 409, statusText: 'Conflict' },
      );
  });

  it('shows a generic conflict message for a 409 from an unrelated call, not the candidate-rejected one', (done) => {
    const service = TestBed.inject(AdminService);

    service.seedTargets(['https://example.com/feed.xml']).subscribe({
      error: () => {
        expect(toastCalls).toEqual([
          { tone: 'danger', text: '加入待探測失敗：Resource already exists' },
        ]);
        done();
      },
    });

    httpMock
      .expectOne('/api/admin/discovery/targets')
      .flush({ detail: 'Resource already exists' }, { status: 409, statusText: 'Conflict' });
  });
});
