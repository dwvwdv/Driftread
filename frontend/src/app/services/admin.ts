import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  DiscoveryCycleSummary,
  DiscoverySource,
  DiscoveryStats,
  Feed,
  FeedCandidate,
  FeedHealthSummary,
  PaginatedDiscoveryTargets,
  PaginatedFeedCandidates,
  PaginatedFeeds,
  RefreshDueSummary,
  RefreshFeedResult,
  SeedTargetsResult,
} from '../models';
import { ToastService } from '../ui/toast/toast';
import { apiMessage, isMissingApiKeyHeader, validationMessage } from '../shared/http-errors';
import { AdminKeyStore } from './admin-key';

/** Body accepted by POST /admin/discovery/candidates/{id}/approve. */
export interface ApproveCandidateBody {
  category: string | null;
  tags: string[];
}

/** Body accepted by POST /admin/discovery/candidates/{id}/reject. */
export interface HoldCandidateBody {
  note: string | null;
}

export interface RejectCandidateBody {
  note: string | null;
  block_host: boolean;
}

export interface DiscoverySourceInput {
  url: string;
  kind: 'links_page' | 'opml';
  label?: string | null;
}

/**
 * Every admin API call.
 *
 * Replaces roughly a dozen hand-built `new HttpHeaders({'x-api-key': ...})` sites
 * and the direct HttpClient use in the old single-page admin component. Two things
 * are centralised here:
 *
 * 1. The key header, sourced from AdminKeyStore, so it exists in one place.
 *
 * 2. Error interpretation. The old code funnelled everything through one `fail()`
 *    that printed `失敗：<detail>`, which flattened several very different
 *    situations into the same unhelpful sentence. Each status now says what
 *    actually happened:
 *
 *      0    the request never reached the backend
 *      403  the key is wrong — clear it and send the operator back to unlock
 *      422  the header was missing entirely (FastAPI's Header(...) is required),
 *           which means the key went empty, e.g. cleared in another tab. Same
 *           recovery as 403.
 *      409  approving a candidate that was already rejected — a state conflict,
 *           not a failure; the caller reloads so the stale row disappears
 *      502  the remote feed could not be fetched. Warning, not danger: nothing on
 *           our side is broken.
 *      503  autonomous discovery is switched off (FEED_DISCOVERY_ENABLED=false).
 *           A configuration state, so it is reported as information.
 *
 * Callers get the error rethrown after it has been reported, so they only need to
 * reset their own loading flags — they do not message the user themselves.
 */
@Injectable({ providedIn: 'root' })
export class AdminService {
  private http = inject(HttpClient);
  private keys = inject(AdminKeyStore);
  private toast = inject(ToastService);
  private router = inject(Router);
  private base = environment.apiUrl;

  // ── Feeds ─────────────────────────────────────────────────────────────────

  listFeeds(page = 1, pageSize = 50, archived?: boolean): Observable<PaginatedFeeds> {
    let params = new HttpParams().set('page', page).set('page_size', pageSize);
    if (archived !== undefined) params = params.set('archived', archived);
    return this.get<PaginatedFeeds>('/admin/feeds', '讀取信息源清單失敗', params);
  }

  listArchived(limit = 200): Observable<Feed[]> {
    return this.get<Feed[]>(
      '/admin/feeds/archived',
      '讀取封存清單失敗',
      new HttpParams().set('limit', limit),
    );
  }

  /** Previously unused by the UI despite existing since the health work landed. */
  listUnhealthy(threshold = 50, limit = 200): Observable<FeedHealthSummary[]> {
    return this.get<FeedHealthSummary[]>(
      '/admin/feeds/unhealthy',
      '讀取健康度清單失敗',
      new HttpParams().set('threshold', threshold).set('limit', limit),
    );
  }

  importFeeds(body: unknown): Observable<Feed[]> {
    return this.post<Feed[]>('/admin/feeds', body, '匯入失敗');
  }

  /** Previously unused by the UI. */
  addFeedFromUrl(feedUrl: string): Observable<Feed> {
    return this.post<Feed>('/admin/feeds/from-url', { feed_url: feedUrl }, '匯入失敗');
  }

  refreshFeed(id: string): Observable<RefreshFeedResult> {
    return this.post<RefreshFeedResult>(`/admin/feeds/${id}/refresh`, {}, '更新文章失敗');
  }

  /** Previously unused by the UI. Drains the scheduler's due queue on demand. */
  refreshDue(limit?: number, maxConcurrency?: number): Observable<RefreshDueSummary> {
    let params = new HttpParams();
    if (limit !== undefined) params = params.set('limit', limit);
    if (maxConcurrency !== undefined) params = params.set('max_concurrency', maxConcurrency);
    return this.post<RefreshDueSummary>('/admin/feeds/refresh-due', {}, '批次更新失敗', params);
  }

  archive(id: string): Observable<Feed> {
    return this.patch<Feed>(`/admin/feeds/${id}/archive`, {}, '封存失敗');
  }

  unarchive(id: string): Observable<Feed> {
    return this.patch<Feed>(`/admin/feeds/${id}/unarchive`, {}, '取消封存失敗');
  }

  // ── Discovery ─────────────────────────────────────────────────────────────

  stats(): Observable<DiscoveryStats> {
    return this.get<DiscoveryStats>('/admin/discovery/stats', '讀取統計失敗');
  }

  runCycle(): Observable<DiscoveryCycleSummary> {
    return this.post<DiscoveryCycleSummary>('/admin/discovery/run', {}, '執行失敗');
  }

  listCandidates(
    page = 1,
    pageSize = 50,
    status: 'pending' | 'held' = 'pending',
  ): Observable<PaginatedFeedCandidates> {
    return this.get<PaginatedFeedCandidates>(
      '/admin/discovery/candidates',
      '讀取候選失敗',
      new HttpParams().set('status', status).set('page', page).set('page_size', pageSize),
    );
  }

  approveCandidate(id: string, body: ApproveCandidateBody): Observable<Feed> {
    return this.post<Feed>(`/admin/discovery/candidates/${id}/approve`, body, '核准失敗');
  }

  holdCandidate(id: string, body: HoldCandidateBody): Observable<FeedCandidate> {
    return this.post<FeedCandidate>(`/admin/discovery/candidates/${id}/hold`, body, '保留失敗');
  }

  rejectCandidate(id: string, body: RejectCandidateBody): Observable<FeedCandidate> {
    return this.post<FeedCandidate>(`/admin/discovery/candidates/${id}/reject`, body, '拒絕失敗');
  }

  seedTargets(urls: string[]): Observable<SeedTargetsResult> {
    return this.post<SeedTargetsResult>('/admin/discovery/targets', { urls }, '加入待探測失敗');
  }

  /** Previously unused by the UI — the frontier was write-only from the console. */
  listTargets(
    status: string | null,
    page = 1,
    pageSize = 50,
  ): Observable<PaginatedDiscoveryTargets> {
    let params = new HttpParams().set('page', page).set('page_size', pageSize);
    if (status) params = params.set('status', status);
    return this.get<PaginatedDiscoveryTargets>(
      '/admin/discovery/targets',
      '讀取待探測佇列失敗',
      params,
    );
  }

  /** Previously unused by the UI. Blocks the whole host, and cannot be undone. */
  blockTarget(id: string): Observable<unknown> {
    return this.patch<unknown>(`/admin/discovery/targets/${id}/block`, {}, '封鎖失敗');
  }

  listSources(): Observable<DiscoverySource[]> {
    return this.get<DiscoverySource[]>('/admin/discovery/sources', '讀取目錄來源失敗');
  }

  addSources(items: DiscoverySourceInput[]): Observable<DiscoverySource[]> {
    return this.post<DiscoverySource[]>('/admin/discovery/sources', { items }, '新增目錄來源失敗');
  }

  updateSource(
    id: string,
    patch: { enabled?: boolean; interval_hours?: number },
  ): Observable<DiscoverySource> {
    return this.patch<DiscoverySource>(`/admin/discovery/sources/${id}`, patch, '更新目錄來源失敗');
  }

  reloadDefaultSources(): Observable<{ loaded: number }> {
    return this.post<{ loaded: number }>(
      '/admin/discovery/sources/reload-defaults',
      {},
      '載入預設來源失敗',
    );
  }

  // ── Plumbing ──────────────────────────────────────────────────────────────

  private get<T>(path: string, context: string, params?: HttpParams): Observable<T> {
    return this.http
      .get<T>(`${this.base}${path}`, { headers: this.headers(), params })
      .pipe(catchError((e) => this.report(e, context)));
  }

  private post<T>(
    path: string,
    body: unknown,
    context: string,
    params?: HttpParams,
  ): Observable<T> {
    return this.http
      .post<T>(`${this.base}${path}`, body, { headers: this.headers(), params })
      .pipe(catchError((e) => this.report(e, context)));
  }

  private patch<T>(path: string, body: unknown, context: string): Observable<T> {
    return this.http
      .patch<T>(`${this.base}${path}`, body, { headers: this.headers() })
      .pipe(catchError((e) => this.report(e, context)));
  }

  private headers(): Record<string, string> {
    return { 'x-api-key': this.keys.key() };
  }

  private report(error: unknown, context: string): Observable<never> {
    const status = error instanceof HttpErrorResponse ? error.status : -1;

    switch (status) {
      case 0:
        this.toast.danger('無法連線到後端服務');
        break;

      // Wrong key. This tab can no longer talk to the admin API, so drop it and
      // go re-enter rather than leaving every panel to fail one at a time.
      case 403:
        this.lockOut();
        break;

      // 422 is ambiguous: FastAPI returns it both for a missing X-API-Key header
      // (Header(...) is required) and for *any* schema violation — a feed import
      // whose JSON lacks a `title`, for instance. Only the former is an auth
      // problem. Clearing the key on the latter would log the operator out
      // because they pasted malformed JSON.
      case 422:
        if (isMissingApiKeyHeader(error)) {
          this.lockOut();
        } else {
          this.toast.danger(`${context}：${validationMessage(error) || '請求內容格式錯誤'}`);
        }
        break;

      case 409:
        this.toast.warning('此候選先前已被拒絕，無法核准');
        break;

      case 502:
        this.toast.warning('來源抓取失敗：遠端沒有回應，或回傳的內容不是有效的 feed');
        break;

      case 503:
        // Not an error — the kill switch is simply off.
        this.toast.info('自主發現目前為停用狀態（FEED_DISCOVERY_ENABLED=false）');
        break;

      default:
        this.toast.danger(`${context}：${apiMessage(error, context)}`);
    }

    return throwError(() => error);
  }

  /** Drops the key for this tab and sends the operator back to re-enter it. */
  private lockOut(): void {
    this.keys.clear();
    this.toast.danger('Admin API Key 無效，請重新輸入');
    void this.router.navigate(['/admin/unlock']);
  }
}
