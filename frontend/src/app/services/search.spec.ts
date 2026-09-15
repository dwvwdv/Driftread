import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { SearchService } from './search';

describe('SearchService', () => {
  let httpMock: HttpTestingController;
  let service: SearchService;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [SearchService, provideHttpClient(), provideHttpClientTesting()],
    });
    httpMock = TestBed.inject(HttpTestingController);
    service = TestBed.inject(SearchService);
  });

  afterEach(() => httpMock.verify());

  it('searchArticles sends q and limit but omits language/cursor when unset', () => {
    service.searchArticles('rust').subscribe();

    const req = httpMock.expectOne((r) => r.url.endsWith('/search/articles'));
    expect(req.request.params.get('q')).toBe('rust');
    expect(req.request.params.get('limit')).toBe('20');
    expect(req.request.params.has('language')).toBe(false);
    expect(req.request.params.has('cursor')).toBe(false);
    req.flush({ items: [], next_cursor: null });
  });

  it('searchArticles includes language and cursor when given', () => {
    service.searchArticles('rust', 'en', 'cursor-1', 10).subscribe();

    const req = httpMock.expectOne((r) => r.url.endsWith('/search/articles'));
    expect(req.request.params.get('language')).toBe('en');
    expect(req.request.params.get('cursor')).toBe('cursor-1');
    expect(req.request.params.get('limit')).toBe('10');
    req.flush({ items: [], next_cursor: null });
  });

  it('searchFeeds sends q against the /search/feeds endpoint', () => {
    service.searchFeeds('things').subscribe();

    const req = httpMock.expectOne((r) => r.url.endsWith('/search/feeds'));
    expect(req.request.params.get('q')).toBe('things');
    req.flush({ items: [], next_cursor: null });
  });
});
