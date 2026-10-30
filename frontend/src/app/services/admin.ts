import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  SourceReview,
  SourceReviewBatchResult,
  GlobalSetting,
  GlobalSettingsResponse,
  ArticleCapSummary,
  ArticleTtlSummary,
  ArticleStorageStats,
  StorageHistory,
  OperationsStatus,
  IngestActivity,
  TableStorageHistory,
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
  RecentAdmission,
  SeedTargetsResult,
} from '../models';
import { ToastService } from '../ui/toast/toast';
import { apiMessage, isMissingApiKeyHeader, validationMessage } from '../shared/http-errors';
import { ManageSessionService } from './manage-session';

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

export type CleanupKind = 'feeds' | 'articles';
export interface DiscoveryControl {
  enabled: boolean;
  effective_enabled: boolean;
  deployment_enabled: boolean;
  version: number;
  updated_at: string | null;
}
export type CleanupAction = 'stage' | 'restore' | 'delete';
export interface CleanupItem {
  id: string;
  title: string;
  url: string;
  feed_id: string | null;
  feed_title: string | null;
  category: string | null;
  group_key: string;
  group_size: number;
  staged_at: string | null;
  article_count: number;
}
export interface ManagementPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export interface ManagementBatch {
  items: {
    id: string;
    ok: boolean;
    error?: string;
    impact?: {
      articles: number;
      bookmarks: number;
      reads: number;
      subscriptions: number;
    };
  }[];
  dry_run?: boolean;
}

/** Management calls carry both the Supabase login and the verified Telegram session. */
@Injectable({ providedIn: 'root' })
export class AdminService {
  private http = inject(HttpClient);
  private keys = inject(ManageSessionService);
  private toast = inject(ToastService);
  private router = inject(Router);
  private base = environment.apiUrl;

  discoveryControl(): Observable<DiscoveryControl> {
    return this.get('/admin/discovery/control', '讀取探索開關失敗');
  }

  setDiscoveryEnabled(enabled: boolean, expectedVersion: number): Observable<DiscoveryControl> {
    return this.patch(
      '/admin/discovery/control',
      { enabled, expected_version: expectedVersion },
      '更新探索開關失敗',
    );
  }

  listCleanup(
    kind: CleanupKind,
    rule: string,
    state: string,
    query: string,
    offset = 0,
  ): Observable<ManagementPage<CleanupItem>> {
    const params = new HttpParams()
      .set('kind', kind)
      .set('rule', rule)
      .set('state', state)
      .set('query', query)
      .set('limit', 50)
      .set('offset', offset);
    return this.get('/admin/cleanup/items', '讀取清理清單失敗', params);
  }

  cleanupBatch(
    kind: CleanupKind,
    ids: string[],
    action: CleanupAction,
    dryRun = true,
  ): Observable<ManagementBatch> {
    return this.post(
      '/admin/cleanup/batch',
      {
        kind,
        ids,
        action,
        dry_run: dryRun,
        confirm_delete: action === 'delete' && !dryRun,
      },
      '批量清理失敗',
    );
  }

  pendingTargets(offset = 0): Observable<ManagementPage<import('../models').DiscoveryTarget>> {
    return this.get(
      '/admin/discovery/pending-targets',
      '讀取待探索清單失敗',
      new HttpParams().set('limit', 50).set('offset', offset),
    );
  }

  removeTargets(ids: string[]): Observable<ManagementBatch> {
    return this.post('/admin/discovery/targets/remove', { ids }, '移除待探索來源失敗');
  }

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

  updateSourceMetadata(
    id: string,
    body: {
      participation_mode: 'normal' | 'signal_only' | 'private';
      fulltext_policy: 'rss' | 'summary_only';
      signal_group: string | null;
      first_party: boolean;
      /** null clears the override and falls back to the article.cap setting. */
      article_cap?: number | null;
    },
  ): Observable<Feed> {
    return this.patch<Feed>(`/admin/feeds/${id}/source`, body, '儲存來源設定失敗');
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
    status: 'pending' | 'held' | 'awaiting_human' = 'pending',
  ): Observable<PaginatedFeedCandidates> {
    return this.get<PaginatedFeedCandidates>(
      '/admin/discovery/candidates',
      '讀取候選失敗',
      new HttpParams().set('status', status).set('page', page).set('page_size', pageSize),
    );
  }

  recentAdmissions(limit = 10): Observable<{ items: RecentAdmission[]; limit: number }> {
    return this.get<{ items: RecentAdmission[]; limit: number }>(
      '/admin/discovery/recent-admissions',
      '讀取近期收錄失敗',
      new HttpParams().set('limit', limit),
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

  operations(): Observable<OperationsStatus> {
    return this.get<OperationsStatus>('/admin/operations', '讀取排程狀態失敗');
  }

  storageStats(): Observable<ArticleStorageStats> {
    return this.get<ArticleStorageStats>('/admin/retention/stats', '讀取儲存統計失敗');
  }

  /**
   * Preview unless dryRun is false. maxPerFeed applies to sources without an
   * override; omitted, the server uses the saved article.cap value.
   */
  runArticleCap(dryRun: boolean, maxPerFeed?: number): Observable<ArticleCapSummary> {
    let params = new HttpParams().set('dry_run', dryRun);
    if (maxPerFeed !== undefined) params = params.set('max_per_feed', maxPerFeed);
    return this.post<ArticleCapSummary>(
      '/admin/retention/article-cap/run',
      {},
      dryRun ? '預覽文章上限失敗' : '執行文章上限失敗',
      params,
    );
  }

  /** Preview by default; applying a batch requires explicit dryRun=false. */
  runArticleTtl(dryRun = true, days?: number, limit?: number): Observable<ArticleTtlSummary> {
    let params = new HttpParams().set('dry_run', dryRun);
    if (days !== undefined) params = params.set('days', days);
    if (limit !== undefined) params = params.set('limit', limit);
    return this.post<ArticleTtlSummary>(
      '/admin/retention/article-ttl/run',
      {},
      dryRun ? '預覽到期文章失敗' : '刪除到期文章失敗',
      params,
    );
  }

  globalSettings(): Observable<GlobalSettingsResponse> {
    return this.get<GlobalSettingsResponse>('/admin/settings', '讀取全域設定失敗');
  }

  saveGlobalSetting<T>(
    key: string,
    value: T,
    expectedVersion: number,
  ): Observable<GlobalSetting<T>> {
    return this.http
      .put<GlobalSetting<T>>(
        `${this.base}/admin/settings/${encodeURIComponent(key)}`,
        {
          value,
          expected_version: expectedVersion,
        },
        { headers: this.headers() },
      )
      .pipe(catchError((error) => this.report(error, '儲存全域設定失敗')));
  }

  tableStorage(days = 7): Observable<TableStorageHistory> {
    return this.get(
      '/admin/retention/tables',
      '讀取每表容量失敗',
      new HttpParams().set('days', days),
    );
  }

  /** `hours` is 24 or 168 (7 days); the API rejects any other window. */
  ingestActivity(limit = 10, hours: 24 | 168 = 24): Observable<IngestActivity> {
    return this.get(
      '/admin/operations/ingest',
      '讀取來源入庫量失敗',
      new HttpParams().set('limit', limit).set('hours', hours),
    );
  }

  storageHistory(): Observable<StorageHistory> {
    return this.get(
      '/admin/retention/history',
      '讀取容量歷史失敗',
      new HttpParams().set('days', 30),
    );
  }

  listSourceReviews(candidateId: string): Observable<{ items: SourceReview[] }> {
    return this.get<{ items: SourceReview[] }>(
      '/admin/source-reviews',
      '讀取來源評審失敗',
      new HttpParams().set('candidate_id', candidateId).set('limit', 1),
    );
  }

  latestSourceReviews(candidateIds: string[]): Observable<{ items: SourceReview[] }> {
    return this.post<{ items: SourceReview[] }>(
      '/admin/source-reviews/latest',
      { candidate_ids: candidateIds },
      '讀取來源評審失敗',
    );
  }

  requestSourceReview(candidateId: string): Observable<SourceReview> {
    return this.post<SourceReview>(
      `/admin/source-reviews/candidates/${candidateId}/request`,
      {},
      '請求來源評審失敗',
    );
  }

  retrySourceReview(reviewId: string): Observable<SourceReview> {
    return this.post<SourceReview>(
      `/admin/source-reviews/${reviewId}/retry`,
      {},
      '重試來源評審失敗',
    );
  }

  resolveSourceReview(
    reviewId: string,
    decision: 'accept' | 'reject' | 'defer',
  ): Observable<SourceReview> {
    return this.post<SourceReview>(
      `/admin/source-reviews/${reviewId}/resolve`,
      { decision },
      '人工覆核失敗',
    );
  }

  batchRequestSourceReviews(
    candidateIds: string[],
    retry = false,
    expectedReviewIds?: Record<string, string>,
  ): Observable<SourceReviewBatchResult> {
    return this.post<SourceReviewBatchResult>(
      '/admin/source-reviews/batch/request',
      {
        candidate_ids: candidateIds,
        retry,
        ...(retry ? { expected_review_ids: expectedReviewIds } : {}),
      },
      retry ? '批次重試來源評審失敗' : '批次請求來源評審失敗',
    );
  }

  batchResolveSourceReviews(
    reviewIds: string[],
    decision: 'accept' | 'reject' | 'defer',
  ): Observable<SourceReviewBatchResult> {
    return this.post<SourceReviewBatchResult>(
      '/admin/source-reviews/batch/resolve',
      { review_ids: reviewIds, decision },
      '批次人工覆核失敗',
    );
  }

  judgeSourceReview(
    reviewId: string,
    assessment: 'agree' | 'disagree',
    decision?: 'accept' | 'reject',
  ): Observable<SourceReview> {
    return this.post<SourceReview>(
      `/admin/source-reviews/${reviewId}/judgment`,
      { assessment, ...(assessment === 'disagree' && decision ? { decision } : {}) },
      '人工決策未完成',
    );
  }

  judgeSourceReviews(
    reviewIds: string[],
    assessment: 'agree' | 'disagree',
    decision?: 'accept' | 'reject',
  ): Observable<SourceReviewBatchResult> {
    return this.post<SourceReviewBatchResult>(
      '/admin/source-reviews/batch/judgment',
      {
        review_ids: reviewIds,
        assessment,
        ...(assessment === 'disagree' && decision ? { decision } : {}),
      },
      '批次人工決策未完成',
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
    return this.keys.headers();
  }

  private report(error: unknown, context: string): Observable<never> {
    const status = error instanceof HttpErrorResponse ? error.status : -1;

    switch (status) {
      case 0:
        this.toast.danger('無法連線到後端服務');
        break;

      // Expired login / Telegram session: return to the login flow.
      case 401:
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

      case 409: {
        const body =
          error instanceof HttpErrorResponse
            ? (error.error as { detail?: { code?: string } } | null)
            : null;
        const code = body && typeof body.detail === 'object' ? body.detail?.code : undefined;
        const manualDecision = context === '保留失敗' || context === '拒絕失敗';
        // Stable codes distinguish admission races from version conflicts.
        // Keep candidate-specific messages scoped to their operations so an
        // unrelated 409 still reports its actual conflict.
        if (manualDecision && code === 'candidate_already_imported') {
          this.toast.warning('此來源已收錄，無法再保留或拒絕候選；請到「信息源管理」操作');
        } else if (manualDecision && code === 'candidate_changed') {
          this.toast.warning('候選狀態已更新，請確認最新清單後再試');
        } else if (context === '核准失敗') {
          this.toast.warning('此候選先前已被拒絕，無法核准');
        } else {
          this.toast.danger(`${context}：${apiMessage(error, '資源衝突，請重新整理後再試')}`);
        }
        break;
      }

      case 502:
        this.toast.warning('來源抓取失敗：遠端沒有回應，或回傳的內容不是有效的 feed');
        break;

      case 503:
        // The discovery switch or management verification service can be unavailable.
        this.toast.info(apiMessage(error, '服務暫時無法使用'));
        break;

      default:
        this.toast.danger(`${context}：${apiMessage(error, context)}`);
    }

    return throwError(() => error);
  }

  /** Clear local management credentials after a server rejection. */
  private lockOut(): void {
    this.keys.clear();
    this.toast.danger('後台驗證已失效，請重新登入');
    void this.router.navigate(['/manage/login']);
  }
}
