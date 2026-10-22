import { Injectable } from '@angular/core';
import { BookmarkType } from '../models';

/** Operations POST /me/sync/operations accepts (backend/routers/sync.py). */
export type OfflineKind = 'read' | 'favorite' | 'read_later';

/** One article of the bounded /me/sync replacement snapshot — summary and
 * personal state only. The snapshot never carries `content`, and only the
 * fields below are kept, so nothing else the server adds later is persisted. */
export interface OfflineArticle {
  id: string;
  feed_id: string;
  feed_title: string | null;
  title: string;
  url: string;
  summary: string | null;
  author: string | null;
  published_at: string | null;
  timeline_at: string | null;
  is_read: boolean;
  bookmark_types: BookmarkType[];
}

/** Desired end state for one (article, kind), coalesced like the extension. */
export interface PendingOperation {
  articleId: string;
  kind: OfflineKind;
  enabled: boolean;
}

/** Everything this device keeps for one signed-in user. */
export interface OfflineAccountRecord {
  userId: string;
  items: OfflineArticle[];
  pending: PendingOperation[];
  cursor: string | null;
  updatedAt: number;
}

/** Thrown when the stored owner is no longer the user a write was made for —
 * an account switch or sign-out happened while the write was in flight. */
export class OwnerChangedError extends Error {
  constructor() {
    super('離線快取的帳號已變更');
    this.name = 'OwnerChangedError';
  }
}

const DB_NAME = 'driftread-offline';
const DB_VERSION = 1;
const ACCOUNTS = 'accounts';
const META = 'meta';
const OWNER_KEY = 'owner';

/**
 * IndexedDB storage for the PWA offline reader, partitioned by user id.
 *
 * Two invariants keep one account's private cache away from another
 * (see docs/CONSUMPTION_SURFACES.md「行動裝置閱讀出口（PWA）」):
 *
 *  - `claim(userId)` deletes every other user's record in the same
 *    transaction that records the new owner, so at most one account's
 *    summaries and pending operations ever exist on the device.
 *  - `update(userId, …)` re-checks the owner inside its own readwrite
 *    transaction and refuses (OwnerChangedError) when it changed, so a late
 *    sync response or queue write for A can never resurrect A's data after
 *    a sign-out or a switch to B.
 *
 * Every read-modify-write runs inside a single transaction using request
 * callbacks (never awaiting other promises mid-transaction), so concurrent
 * tabs cannot lose each other's queued operations.
 */
@Injectable({ providedIn: 'root' })
export class OfflineStore {
  private db: Promise<IDBDatabase> | null = null;

  available(): boolean {
    return typeof indexedDB !== 'undefined';
  }

  /** Records `userId` as the device's only owner, dropping everyone else. */
  claim(userId: string): Promise<OfflineAccountRecord | null> {
    return this.transaction<OfflineAccountRecord | null>('readwrite', (accounts, meta, finish) => {
      const keys = accounts.getAllKeys();
      keys.onsuccess = () => {
        for (const key of keys.result) if (key !== userId) accounts.delete(key);
        meta.put(userId, OWNER_KEY);
        const current = accounts.get(userId);
        current.onsuccess = () => finish((current.result as OfflineAccountRecord) ?? null);
      };
    });
  }

  /** User id the stored cache belongs to, if any. */
  owner(): Promise<string | null> {
    return this.transaction<string | null>('readonly', (_accounts, meta, finish) => {
      const owner = meta.get(OWNER_KEY);
      owner.onsuccess = () => finish((owner.result as string) ?? null);
    });
  }

  read(userId: string): Promise<OfflineAccountRecord | null> {
    return this.transaction<OfflineAccountRecord | null>('readonly', (accounts, _meta, finish) => {
      const current = accounts.get(userId);
      current.onsuccess = () => finish((current.result as OfflineAccountRecord) ?? null);
    });
  }

  /**
   * Atomic read-modify-write of `userId`'s record. `change` receives the stored
   * record (or null) and returns the replacement, or null to leave it as is.
   * Rejects with OwnerChangedError when `userId` is no longer the owner.
   */
  update(
    userId: string,
    change: (current: OfflineAccountRecord | null) => OfflineAccountRecord | null,
  ): Promise<OfflineAccountRecord | null> {
    return this.transaction<OfflineAccountRecord | null>(
      'readwrite',
      (accounts, meta, finish, abort) => {
        const owner = meta.get(OWNER_KEY);
        owner.onsuccess = () => {
          if (owner.result !== userId) {
            abort(new OwnerChangedError());
            return;
          }
          const current = accounts.get(userId);
          current.onsuccess = () => {
            const stored = (current.result as OfflineAccountRecord) ?? null;
            let next: OfflineAccountRecord | null;
            try {
              next = change(stored);
            } catch (error) {
              abort(error instanceof Error ? error : new Error(String(error)));
              return;
            }
            if (next === null) {
              finish(stored);
              return;
            }
            if (next.userId !== userId) {
              abort(new OwnerChangedError());
              return;
            }
            accounts.put(next);
            finish(next);
          };
        };
      },
    );
  }

  /** Removes every account's cache and queue (sign-out). */
  clear(): Promise<void> {
    return this.transaction<void>('readwrite', (accounts, meta, finish) => {
      accounts.clear();
      meta.clear();
      finish(undefined);
    });
  }

  private open(): Promise<IDBDatabase> {
    if (!this.db) {
      this.db = new Promise<IDBDatabase>((resolve, reject) => {
        if (!this.available()) {
          reject(new Error('IndexedDB unavailable'));
          return;
        }
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(ACCOUNTS)) {
            db.createObjectStore(ACCOUNTS, { keyPath: 'userId' });
          }
          if (!db.objectStoreNames.contains(META)) db.createObjectStore(META);
        };
        request.onsuccess = () => {
          const db = request.result;
          // A future schema upgrade in another tab must not stay blocked.
          db.onversionchange = () => {
            db.close();
            this.db = null;
          };
          resolve(db);
        };
        request.onerror = () => reject(request.error ?? new Error('IndexedDB open failed'));
      });
      this.db.catch(() => (this.db = null));
    }
    return this.db;
  }

  private async transaction<T>(
    mode: IDBTransactionMode,
    work: (
      accounts: IDBObjectStore,
      meta: IDBObjectStore,
      finish: (value: T) => void,
      abort: (error: Error) => void,
    ) => void,
  ): Promise<T> {
    const db = await this.open();
    return new Promise<T>((resolve, reject) => {
      const tx = db.transaction([ACCOUNTS, META], mode);
      let result: T;
      let failure: Error | null = null;
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(failure ?? tx.error ?? new Error('IndexedDB transaction aborted'));
      const abort = (error: Error) => {
        failure = error;
        try {
          tx.abort();
        } catch {
          reject(error);
        }
      };
      try {
        work(tx.objectStore(ACCOUNTS), tx.objectStore(META), (value) => (result = value), abort);
      } catch (error) {
        abort(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }
}
