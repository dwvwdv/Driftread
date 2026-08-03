import { TestBed } from '@angular/core/testing';
import { AdminKeyStore } from './admin-key';

const STORAGE_KEY = 'driftread_admin_key';

describe('AdminKeyStore', () => {
  beforeEach(() => {
    sessionStorage.clear();
    TestBed.resetTestingModule();
  });

  it('starts locked when nothing is stored', () => {
    const store = TestBed.inject(AdminKeyStore);
    expect(store.hasKey()).toBe(false);
    expect(store.key()).toBe('');
  });

  it('persists to sessionStorage so the key survives a reload', () => {
    // This is the whole reason the admin sub-pages can load on init instead of
    // needing a manual "load" button on every panel.
    const store = TestBed.inject(AdminKeyStore);
    store.set('secret-key');

    expect(store.hasKey()).toBe(true);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBe('secret-key');
  });

  it('reads an existing key on construction', () => {
    sessionStorage.setItem(STORAGE_KEY, 'from-storage');
    const store = TestBed.inject(AdminKeyStore);

    expect(store.key()).toBe('from-storage');
    expect(store.hasKey()).toBe(true);
  });

  it('trims surrounding whitespace, which a pasted key usually carries', () => {
    const store = TestBed.inject(AdminKeyStore);
    store.set('  padded  ');
    expect(store.key()).toBe('padded');
  });

  it('treats a whitespace-only key as no key at all', () => {
    const store = TestBed.inject(AdminKeyStore);
    store.set('   ');
    expect(store.hasKey()).toBe(false);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('clear removes it from storage as well as memory', () => {
    const store = TestBed.inject(AdminKeyStore);
    store.set('secret-key');
    store.clear();

    expect(store.hasKey()).toBe(false);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
