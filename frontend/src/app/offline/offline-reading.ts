import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from '../services/auth';
import { ReadingStreamService } from '../services/reading-stream';
import { NetworkStatusService } from './network-status';
import {
  CACHE_TTL_MS,
  SyncSnapshot,
  acknowledge,
  applySnapshot,
  emptyRecord,
  isFresh,
  overlay,
  queueOperation,
} from './offline-state';
import {
  OfflineAccountRecord,
  OfflineKind,
  OfflineStore,
  OwnerChangedError,
} from './offline-store';

/** Background refresh only when the cache is at least this old. */
export const AUTO_REFRESH_MS = 15 * 60 * 1000;

/**
 * - `session`: a signed-in user's own cache (read and queue).
 * - `dormant`: no session could be restored (e.g. the access token expired
 *   while offline and refresh failed), but no sign-out happened either. The
 *   last owner's cache is shown read-only while the device is offline.
 * - `none`: nothing to show.
 */
export type OfflineMode = 'none' | 'session' | 'dormant';

export type QueueResult = { queued: false } | { queued: true; replay: Promise<SyncResult> | null };

export interface SyncResult {
  ok: boolean;
  /** Queued operations the server rejected (404/409: article no longer available). */
  dropped: number;
}

/**
 * The PWA's offline reader: /me/sync replacement snapshot and
 * /me/sync/operations replay with the extension's semantics, persisted per
 * user in IndexedDB (OfflineStore). Design and trade-offs are in
 * docs/CONSUMPTION_SURFACES.md「行動裝置閱讀出口（PWA）」.
 *
 * Account isolation:
 *  - the identity effect claims the store for the signed-in user (dropping any
 *    other user's cache and queue) and clears everything when a session ends
 *    in this runtime (sign-out, revoked refresh token);
 *  - every request carries the token captured at sync start explicitly (the
 *    interceptor leaves an existing Authorization header alone), and a
 *    generation/user check before each request stops a replay the moment the
 *    account changes — A's queue is never sent with B's token;
 *  - store writes re-check the owner, so late responses cannot resurrect a
 *    cleared account.
 */
@Injectable({ providedIn: 'root' })
export class OfflineReadingService {
  private auth = inject(AuthService);
  private http = inject(HttpClient);
  private store = inject(OfflineStore);
  private network = inject(NetworkStatusService);
  private stream = inject(ReadingStreamService);
  private base = environment.apiUrl;

  private readonly _record = signal<OfflineAccountRecord | null>(null);
  private readonly _mode = signal<OfflineMode>('none');
  private readonly _syncing = signal(false);
  private readonly _error = signal<string | null>(null);
  private readonly _loaded = signal(false);
  /** Wall clock for the TTL. Date.now() is not reactive, so this is bumped
   * when the shown cache expires and whenever the page becomes visible again
   * (a suspended PWA or a tab left open must not keep showing an expired cache). */
  private readonly _now = signal(Date.now());

  readonly mode = this._mode.asReadonly();
  readonly syncing = this._syncing.asReadonly();
  readonly error = this._error.asReadonly();
  /** True once the store has been read for the current identity. */
  readonly loaded = this._loaded.asReadonly();
  readonly online = this.network.online;

  /** The record the UI may show: dormant caches only while offline, and
   * nothing older than the 24-hour TTL. */
  readonly record = computed(() => {
    const record = this._record();
    if (!record || !isFresh(record, this._now())) return null;
    if (this._mode() === 'dormant' && this.network.online()) return null;
    return record;
  });
  /** A cache exists but is past its TTL — "connect and sync" instead of items. */
  readonly expired = computed(() => {
    const record = this._record();
    return !!record && record.updatedAt > 0 && !isFresh(record, this._now());
  });
  readonly items = computed(() => {
    const record = this.record();
    return record ? overlay(record) : [];
  });
  readonly pendingCount = computed(() => this._record()?.pending.length ?? 0);
  readonly updatedAt = computed(() => this._record()?.updatedAt || null);

  /** undefined until the first identity is observed in this runtime. */
  private owner: string | null | undefined = undefined;
  private generation = 0;
  private claimed: Promise<void> = Promise.resolve();
  private inFlight: Promise<SyncResult> | null = null;
  private again = false;
  private wasOnline = this.network.online();

  constructor() {
    const destroyRef = inject(DestroyRef);
    let expiryTimer: ReturnType<typeof setTimeout> | undefined;
    effect(() => {
      // Re-evaluate the TTL exactly when the current cache expires.
      const updatedAt = this._record()?.updatedAt ?? 0;
      clearTimeout(expiryTimer);
      expiryTimer = undefined;
      if (updatedAt <= 0) return;
      const delay = updatedAt + CACHE_TTL_MS - Date.now();
      if (delay <= 0) {
        untracked(() => this._now.set(Date.now()));
        return;
      }
      expiryTimer = setTimeout(() => this._now.set(Date.now()), delay + 1);
    });
    const document = inject(DOCUMENT);
    const onVisible = () => {
      if (document.visibilityState === 'visible') this._now.set(Date.now());
    };
    document.addEventListener('visibilitychange', onVisible);
    destroyRef.onDestroy(() => {
      clearTimeout(expiryTimer);
      document.removeEventListener('visibilitychange', onVisible);
    });
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      untracked(() => this.onIdentity(userId));
    });
    effect(() => {
      const online = this.network.online();
      untracked(() => {
        const reconnected = online && !this.wasOnline;
        this.wasOnline = online;
        if (reconnected) this.onReconnect();
      });
    });
  }

  /** Sign-out: drop every account's cache and queue before the session ends. */
  async clearAll(): Promise<void> {
    this.generation++;
    this._record.set(null);
    this._mode.set('none');
    this._error.set(null);
    if (!this.store.available()) return;
    try {
      await this.store.clear();
    } catch {
      // Nothing more we can do; the next claim() still drops other users.
    }
  }

  /**
   * Pull the snapshot, replay queued operations with this account's token,
   * then pull the authoritative snapshot again. Concurrent calls share one run.
   * Also the opt-in: the first successful sync creates the device cache.
   */
  sync(): Promise<SyncResult> {
    if (this.inFlight) {
      // An operation queued mid-run may have missed this run's replay list.
      this.again = true;
      return this.inFlight;
    }
    const run = async () => {
      let result: SyncResult;
      do {
        this.again = false;
        result = await this.runSync();
      } while (this.again && result.ok);
      return result;
    };
    this.inFlight = run().finally(() => (this.inFlight = null));
    return this.inFlight;
  }

  /**
   * Optimistically sets read / favorite / read-later for a cached article and
   * queues it; online, the queue is replayed right away (`replay`). Returns
   * `queued: false` (view unchanged) when the operation cannot be queued. A failed
   * IndexedDB write rolls the optimistic state back; a 404/409 on replay
   * drops the operation, so the view falls back to the server snapshot.
   */
  async setState(articleId: string, kind: OfflineKind, enabled: boolean): Promise<QueueResult> {
    const userId = this.auth.session()?.user?.id;
    const previous = this.record();
    if (!userId || this._mode() !== 'session' || !previous || previous.userId !== userId) {
      return { queued: false };
    }
    let optimistic: OfflineAccountRecord;
    try {
      optimistic = queueOperation(previous, articleId, kind, enabled);
    } catch (error) {
      this._error.set(error instanceof Error ? error.message : '無法排入離線操作');
      return { queued: false };
    }
    const generation = this.generation;
    this._record.set(optimistic);
    try {
      const saved = await this.store.update(userId, (current) =>
        queueOperation(current ?? previous, articleId, kind, enabled),
      );
      if (generation === this.generation) this._record.set(saved);
    } catch (error) {
      if (generation === this.generation) {
        // Roll back only our own optimistic write; anything newer is re-read.
        if (this._record() === optimistic) this._record.set(previous);
        else this._record.set(await this.store.read(userId).catch(() => previous));
        if (!(error instanceof OwnerChangedError)) {
          this._error.set(error instanceof Error ? error.message : '離線操作儲存失敗');
        }
      }
      return { queued: false };
    }
    return { queued: true, replay: this.network.online() ? this.sync() : null };
  }

  private onIdentity(userId: string | null): void {
    const previous = this.owner;
    if (previous === userId) return;
    this.owner = userId;
    this.generation++;
    const generation = this.generation;
    this._record.set(null);
    this._error.set(null);
    this._loaded.set(false);
    if (!this.store.available()) {
      this._mode.set('none');
      this._loaded.set(true);
      return;
    }
    if (userId) {
      this._mode.set('session');
      this.claimed = this.claim(userId, generation);
    } else if (previous) {
      // The session ended in this runtime: sign-out or a revoked refresh token.
      this._mode.set('none');
      this.claimed = this.store.clear().catch(() => undefined);
      this._loaded.set(true);
    } else {
      this.claimed = this.loadDormant(generation);
    }
  }

  private async claim(userId: string, generation: number): Promise<void> {
    try {
      const record = await this.store.claim(userId);
      if (generation !== this.generation) return;
      this._record.set(record);
      this._loaded.set(true);
      if (record && this.network.online() && this.needsSync(record)) void this.sync();
    } catch {
      if (generation !== this.generation) return;
      this._error.set('這個瀏覽器無法使用離線儲存');
      this._loaded.set(true);
    }
  }

  private async loadDormant(generation: number): Promise<void> {
    try {
      const owner = await this.store.owner();
      const record = owner ? await this.store.read(owner) : null;
      if (generation !== this.generation) return;
      this._mode.set(record ? 'dormant' : 'none');
      this._record.set(record);
    } catch {
      if (generation === this.generation) this._mode.set('none');
    } finally {
      if (generation === this.generation) this._loaded.set(true);
    }
  }

  private needsSync(record: OfflineAccountRecord): boolean {
    return record.pending.length > 0 || Date.now() - record.updatedAt >= AUTO_REFRESH_MS;
  }

  private onReconnect(): void {
    const record = this._record();
    if (this._mode() === 'session' && record && this.needsSync(record)) void this.sync();
  }

  private async runSync(): Promise<SyncResult> {
    const session = this.auth.session();
    const userId = session?.user?.id;
    const token = session?.access_token;
    if (!userId || !token) {
      this._error.set('請先登入再同步');
      return { ok: false, dropped: 0 };
    }
    if (!this.network.online()) {
      this._error.set('目前離線，待同步操作會在重新連線後送出');
      return { ok: false, dropped: 0 };
    }
    await this.claimed;
    const generation = this.generation;
    const guard = () => {
      if (generation !== this.generation || this.auth.session()?.user?.id !== userId) {
        throw new OwnerChangedError();
      }
    };
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    const commit = async (change: (r: OfflineAccountRecord) => OfflineAccountRecord) => {
      guard();
      const next = await this.store.update(userId, (current) =>
        change(current ?? emptyRecord(userId)),
      );
      guard();
      this._record.set(next);
      return next as OfflineAccountRecord;
    };
    const pull = async (record: OfflineAccountRecord) => {
      const requested = new Set(record.pending.map((p) => p.articleId));
      let params = new HttpParams();
      if (record.cursor) params = params.set('cursor', record.cursor);
      for (const id of requested) params = params.append('pending_ids', id);
      guard();
      const response = await firstValueFrom(
        this.http.get<SyncSnapshot>(`${this.base}/me/sync`, { params, headers }),
      );
      if (response.account_id !== userId) throw new OwnerChangedError();
      return commit((current) => applySnapshot(current, response, requested));
    };

    this._syncing.set(true);
    this._error.set(null);
    let dropped = 0;
    try {
      // Apply invalidations (deletions, revoked rights) before replaying.
      let record = await pull(await commit((current) => current));
      const operations = [...record.pending];
      for (const operation of operations) {
        guard();
        try {
          await firstValueFrom(
            this.http.post<void>(
              `${this.base}/me/sync/operations`,
              { article_id: operation.articleId, kind: operation.kind, enabled: operation.enabled },
              { headers },
            ),
          );
        } catch (error) {
          if (!(error instanceof HttpErrorResponse && [404, 409].includes(error.status))) {
            throw error;
          }
          dropped++;
        }
        record = await commit((current) => acknowledge(current, operation));
      }
      if (operations.length > 0) {
        await pull(record);
        this.stream.loadCounts();
      }
      return { ok: true, dropped };
    } catch (error) {
      if (error instanceof OwnerChangedError) return { ok: false, dropped };
      if (generation === this.generation) this._error.set(syncErrorMessage(error));
      return { ok: false, dropped };
    } finally {
      if (generation === this.generation) this._syncing.set(false);
    }
  }
}

function syncErrorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 0) return '無法連線，待同步操作會保留到下次同步';
    if (error.status === 401 || error.status === 403) return '登入已失效，請重新登入後再同步';
    if (error.status === 503) return '伺服器忙碌，待同步操作會保留，稍後再試';
    return `同步失敗（${error.status}），待同步操作會保留`;
  }
  return '同步失敗，待同步操作會保留';
}
