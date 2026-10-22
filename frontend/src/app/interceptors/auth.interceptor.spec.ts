import { HttpClient, HttpHeaders, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { environment } from '../../environments/environment';
import { AuthService } from '../services/auth';
import { authInterceptor } from './auth.interceptor';

describe('authInterceptor', () => {
  let http: HttpClient;
  let controller: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: { accessToken: 'current-token' } },
      ],
    });
    http = TestBed.inject(HttpClient);
    controller = TestBed.inject(HttpTestingController);
  });

  afterEach(() => controller.verify());

  it('attaches the current token to own API calls only', () => {
    http.get(`${environment.apiUrl}/me/feeds`).subscribe();
    http.get('https://third-party.example/feed').subscribe();
    expect(
      controller.expectOne(`${environment.apiUrl}/me/feeds`).request.headers.get('Authorization'),
    ).toBe('Bearer current-token');
    expect(
      controller.expectOne('https://third-party.example/feed').request.headers.has('Authorization'),
    ).toBe(false);
  });

  it('keeps an explicitly pinned token (offline replay for the queue owner)', () => {
    http
      .post(
        `${environment.apiUrl}/me/sync/operations`,
        {},
        {
          headers: new HttpHeaders({ Authorization: 'Bearer owner-token' }),
        },
      )
      .subscribe();
    const req = controller.expectOne(`${environment.apiUrl}/me/sync/operations`);
    expect(req.request.headers.get('Authorization')).toBe('Bearer owner-token');
  });

  it('never adds the user token to admin API calls', () => {
    http.get(`${environment.apiUrl}/admin/feeds`).subscribe();
    expect(
      controller
        .expectOne(`${environment.apiUrl}/admin/feeds`)
        .request.headers.has('Authorization'),
    ).toBe(false);
  });
});
