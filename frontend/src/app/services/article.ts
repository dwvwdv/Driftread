import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Article, PaginatedFeedArticles } from '../models';

@Injectable({ providedIn: 'root' })
export class ArticleService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  /**
   * A feed's full article list (TODO.md "Feed 完整文章列表"), cursor
   * (keyset) paginated the same way GET /me/stream is — see
   * routers/articles.py for why offset pagination was replaced. Public
   * endpoint; the auth interceptor still attaches a bearer token when the
   * caller is signed in, so each row's `is_read`/`is_bookmarked` reflects
   * their own state.
   */
  getArticles(feedId: string, cursor?: string | null, limit = 20): Observable<PaginatedFeedArticles> {
    let params = new HttpParams().set('limit', limit);
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<PaginatedFeedArticles>(`${this.base}/feeds/${feedId}/articles`, { params });
  }

  getArticle(id: string): Observable<Article> {
    return this.http.get<Article>(`${this.base}/articles/${id}`);
  }
}
