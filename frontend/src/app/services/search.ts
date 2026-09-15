import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { PaginatedArticleSearchResults, PaginatedFeedSearchResults } from '../models';

/**
 * Full-text search (TODO.md P2 「全文搜尋」). Two independent endpoints, not
 * one merged "search everything" call — the backend keeps article and feed
 * matches separate (see routers/search.py), and so does this service.
 */
@Injectable({ providedIn: 'root' })
export class SearchService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  searchArticles(
    q: string,
    language?: string | null,
    cursor?: string | null,
    limit = 20,
  ): Observable<PaginatedArticleSearchResults> {
    let params = new HttpParams().set('q', q).set('limit', limit);
    if (language) params = params.set('language', language);
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<PaginatedArticleSearchResults>(`${this.base}/search/articles`, { params });
  }

  searchFeeds(
    q: string,
    language?: string | null,
    cursor?: string | null,
    limit = 20,
  ): Observable<PaginatedFeedSearchResults> {
    let params = new HttpParams().set('q', q).set('limit', limit);
    if (language) params = params.set('language', language);
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<PaginatedFeedSearchResults>(`${this.base}/search/feeds`, { params });
  }
}
