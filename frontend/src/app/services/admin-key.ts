import { Injectable, computed, signal } from '@angular/core';

const STORAGE_KEY = 'driftread_admin_key';

/**
 * Holds the admin API key for the current tab.
 *
 * Backed by sessionStorage rather than localStorage. Per-tab and gone when the
 * browser closes is the right lifetime for a shared operator secret — and it is
 * also what makes the redesigned admin usable at all: the key used to live in a
 * plain in-memory signal, so every refresh wiped it and every panel had to be
 * re-loaded by hand. Surviving F5 is what lets sub-pages just load on init.
 *
 * Rules that go with it, and that the UI upholds:
 *   - never placed in a URL or query parameter
 *   - never logged
 *   - never rendered back (the sidebar shows an unlocked indicator, not the value)
 *   - entered through <input type="password" autocomplete="off">
 *
 * Threat note: script running on this origin can read sessionStorage. The key is
 * typed into this origin regardless, so persisting it widens the window, not the
 * class of exposure.
 */
@Injectable({ providedIn: 'root' })
export class AdminKeyStore {
  private _key = signal<string>(this.read());

  key = this._key.asReadonly();
  hasKey = computed(() => this._key().length > 0);

  set(key: string): void {
    const trimmed = key.trim();
    this._key.set(trimmed);
    try {
      if (trimmed) {
        sessionStorage.setItem(STORAGE_KEY, trimmed);
      } else {
        sessionStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // Storage can be unavailable (private mode, disabled cookies). The key
      // still works for this page view, it just will not survive a reload.
    }
  }

  clear(): void {
    this.set('');
  }

  private read(): string {
    try {
      return sessionStorage.getItem(STORAGE_KEY) ?? '';
    } catch {
      return '';
    }
  }
}
