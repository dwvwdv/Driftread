import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Article, PaginatedArticles } from '../models';

@Injectable({ providedIn: 'root' })
export class ArticleService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  getArticles(feedId: string, page = 1, pageSize = 20): Observable<PaginatedArticles> {
    const params = new HttpParams().set('page', page).set('page_size', pageSize);
    return this.http.get<PaginatedArticles>(`${this.base}/feeds/${feedId}/articles`, { params });
  }

  getArticle(id: string): Observable<Article> {
    return this.http.get<Article>(`${this.base}/articles/${id}`);
  }
}
