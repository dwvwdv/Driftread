import { Injectable, signal } from '@angular/core';

export type ThemeMode = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'driftread_theme';

/**
 * Light/dark theme control.
 *
 * The actual colours live in CSS custom properties (src/styles/_tokens.scss); all
 * this does is decide which of the three selectors wins:
 *
 *   :root                          → dark (the default)
 *   :root:not([data-theme])        → light, when the OS asks for light
 *   :root[data-theme='light'|'dark'] → explicit user choice, beats the OS
 *
 * So 'system' means *removing* the attribute, not setting it to something.
 *
 * The same value is read by the inline bootstrap script in index.html, which
 * applies it before first paint so a light-mode user never sees a dark flash.
 * Keep the storage key and the attribute name in sync with that script.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private _mode = signal<ThemeMode>(this.read());
  mode = this._mode.asReadonly();

  constructor() {
    this.apply(this._mode());
  }

  set(mode: ThemeMode): void {
    this._mode.set(mode);
    try {
      if (mode === 'system') {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, mode);
      }
    } catch {
      // Private browsing can reject writes. The theme still applies for this
      // page view; it just will not survive a reload.
    }
    this.apply(mode);
  }

  private read(): ThemeMode {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored === 'light' || stored === 'dark' ? stored : 'system';
    } catch {
      return 'system';
    }
  }

  private apply(mode: ThemeMode): void {
    const root = document.documentElement;
    // setAttribute rather than `root.dataset.theme`: noPropertyAccessFromIndexSignature
    // rejects dotted access on DOMStringMap.
    if (mode === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', mode);
    }
  }
}
