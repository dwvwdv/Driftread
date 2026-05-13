import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Article, BookmarkType, Feed, OpmlImportResult, UserPreferences } from '../models';

@Injectable({ providedIn: 'root' })
export class MeService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  listSubscriptions(): Observable<Feed[]> {
    return this.http.get<Feed[]>(`${this.base}/me/feeds`);
  }

  subscribe(feedId: string): Observable<void> {
    return this.http.post<void>(`${this.base}/me/feeds/${feedId}`, {});
  }

  unsubscribe(feedId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/me/feeds/${feedId}`);
  }

  markRead(articleId: string): Observable<void> {
    return this.http.post<void>(`${this.base}/me/articles/${articleId}/read`, {});
  }

  listReads(): Observable<string[]> {
    return this.http.get<string[]>(`${this.base}/me/reads`);
  }

  listBookmarks(bookmarkType: BookmarkType = 'favorite'): Observable<Article[]> {
    const params = new HttpParams().set('bookmark_type', bookmarkType);
    return this.http.get<Article[]>(`${this.base}/me/bookmarks`, { params });
  }

  addBookmark(articleId: string, bookmarkType: BookmarkType): Observable<void> {
    return this.http.post<void>(`${this.base}/me/bookmarks`, {
      article_id: articleId,
      bookmark_type: bookmarkType,
    });
  }

  removeBookmark(articleId: string, bookmarkType: BookmarkType): Observable<void> {
    const params = new HttpParams().set('bookmark_type', bookmarkType);
    return this.http.delete<void>(`${this.base}/me/bookmarks/${articleId}`, { params });
  }

  getPreferences(): Observable<UserPreferences> {
    return this.http.get<UserPreferences>(`${this.base}/me/preferences`);
  }

  updatePreferences(prefs: UserPreferences): Observable<UserPreferences> {
    return this.http.put<UserPreferences>(`${this.base}/me/preferences`, prefs);
  }

  importOpml(file: File): Observable<OpmlImportResult> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<OpmlImportResult>(`${this.base}/me/import/opml`, form);
  }

  exportOpmlUrl(): string {
    return `${this.base}/me/export/opml`;
  }
}
