import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  ArticleSummary,
  BookmarkType,
  Feed,
  MarkAllReadRequest,
  MarkAllReadResult,
  OpmlImportResult,
  PaginatedReads,
  PaginatedStream,
  UnreadSummary,
  UserPreferences,
} from '../models';

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

  markUnread(articleId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/me/articles/${articleId}/read`);
  }

  markAllRead(body: MarkAllReadRequest): Observable<MarkAllReadResult> {
    return this.http.post<MarkAllReadResult>(`${this.base}/me/reads/mark-all`, body);
  }

  listReads(cursor?: string, limit = 100): Observable<PaginatedReads> {
    let params = new HttpParams().set('limit', limit);
    if (cursor) {
      params = params.set('cursor', cursor);
    }
    return this.http.get<PaginatedReads>(`${this.base}/me/reads`, { params });
  }

  /**
   * The aggregated article stream (TODO.md "我的閱讀流"): every article from
   * every feed the caller is subscribed to, cursor-paginated and sorted by
   * publish date, with `feed_id` / `unread_only` filters and each row's read
   * state joined in.
   */
  getStream(options: {
    cursor?: string | null;
    limit?: number;
    feedId?: string | null;
    unreadOnly?: boolean;
  }): Observable<PaginatedStream> {
    let params = new HttpParams().set('limit', options.limit ?? 30);
    if (options.cursor) params = params.set('cursor', options.cursor);
    if (options.feedId) params = params.set('feed_id', options.feedId);
    if (options.unreadOnly) params = params.set('unread_only', true);
    return this.http.get<PaginatedStream>(`${this.base}/me/stream`, { params });
  }

  getUnreadCounts(): Observable<UnreadSummary> {
    return this.http.get<UnreadSummary>(`${this.base}/me/stream/unread-counts`);
  }

  listBookmarks(bookmarkType: BookmarkType = 'favorite'): Observable<ArticleSummary[]> {
    const params = new HttpParams().set('bookmark_type', bookmarkType);
    return this.http.get<ArticleSummary[]>(`${this.base}/me/bookmarks`, { params });
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

  /**
   * Downloads the subscription OPML.
   *
   * This used to be `exportOpmlUrl()`, a plain string dropped into an
   * `<a [href]>`. Following that link is a browser navigation, not an HttpClient
   * request, so the auth interceptor never saw it and no Authorization header was
   * sent — while GET /api/me/export/opml requires a JWT. The export therefore
   * 401'd for every user, every time.
   *
   * Going through HttpClient gets the token attached; the caller turns the blob
   * into a download.
   */
  exportOpml(): Observable<Blob> {
    return this.http.get(`${this.base}/me/export/opml`, { responseType: 'blob' });
  }
}
