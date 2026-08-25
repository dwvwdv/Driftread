import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Feed, FeedWithArticles, PaginatedFeeds } from '../models';

@Injectable({ providedIn: 'root' })
export class FeedService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  getFeeds(
    page = 1,
    pageSize = 20,
    category?: string,
    language?: string,
    tag?: string,
    search?: string,
  ): Observable<PaginatedFeeds> {
    let params = new HttpParams().set('page', page).set('page_size', pageSize);
    if (category) params = params.set('category', category);
    if (language) params = params.set('language', language);
    if (tag) params = params.set('tag', tag);
    if (search) params = params.set('search', search);
    return this.http.get<PaginatedFeeds>(`${this.base}/feeds`, { params });
  }

  getFeed(id: string): Observable<FeedWithArticles> {
    return this.http.get<FeedWithArticles>(`${this.base}/feeds/${id}`);
  }

  getCategories(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/feeds/categories`);
  }

  getLanguages(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/feeds/languages`);
  }
}
