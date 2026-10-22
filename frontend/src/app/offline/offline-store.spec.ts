import { IDBFactory } from 'fake-indexeddb';
import { OfflineAccountRecord, OfflineStore, OwnerChangedError } from './offline-store';

const record = (
  userId: string,
  extra: Partial<OfflineAccountRecord> = {},
): OfflineAccountRecord => ({
  userId,
  items: [],
  pending: [],
  cursor: null,
  updatedAt: 1,
  ...extra,
});

describe('OfflineStore (IndexedDB)', () => {
  let store: OfflineStore;

  beforeEach(() => {
    // A brand-new in-memory IndexedDB for every test.
    globalThis.indexedDB = new IDBFactory();
    store = new OfflineStore();
  });

  it('partitions by user id and keeps only the claimed owner', async () => {
    await store.claim('user-a');
    await store.update('user-a', () =>
      record('user-a', { pending: [{ articleId: 'x', kind: 'read', enabled: true }] }),
    );
    expect((await store.read('user-a'))?.pending).toHaveLength(1);

    // Switching account removes A's summaries and pending queue.
    expect(await store.claim('user-b')).toBeNull();
    expect(await store.read('user-a')).toBeNull();
    expect(await store.owner()).toBe('user-b');
  });

  it('refuses writes for a user that is no longer the owner', async () => {
    await store.claim('user-a');
    await store.claim('user-b');
    await expect(store.update('user-a', () => record('user-a'))).rejects.toBeInstanceOf(
      OwnerChangedError,
    );
    expect(await store.read('user-a')).toBeNull();
  });

  it('refuses to store a record under another user id', async () => {
    await store.claim('user-a');
    await expect(store.update('user-a', () => record('user-b'))).rejects.toBeInstanceOf(
      OwnerChangedError,
    );
  });

  it('clear() removes every account and the owner, so late writes cannot resurrect data', async () => {
    await store.claim('user-a');
    await store.update('user-a', () => record('user-a'));
    await store.clear();
    expect(await store.owner()).toBeNull();
    await expect(store.update('user-a', () => record('user-a'))).rejects.toBeInstanceOf(
      OwnerChangedError,
    );
    expect(await store.read('user-a')).toBeNull();
  });

  it('a throwing change aborts the transaction without writing', async () => {
    await store.claim('user-a');
    await store.update('user-a', () => record('user-a', { cursor: 'kept' }));
    await expect(
      store.update('user-a', () => {
        throw new Error('limit');
      }),
    ).rejects.toThrow('limit');
    expect((await store.read('user-a'))?.cursor).toBe('kept');
  });

  it('null from change leaves the stored record untouched', async () => {
    await store.claim('user-a');
    expect(await store.update('user-a', () => null)).toBeNull();
    expect(await store.read('user-a')).toBeNull();
  });
});
