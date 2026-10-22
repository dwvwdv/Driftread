import { DestroyRef, Injectable, inject, signal } from '@angular/core';

/**
 * `navigator.onLine` as a signal. It only reports "no network interface" —
 * a captive portal still reads as online — so callers treat it as a hint and
 * still handle request failures (status 0) as offline.
 */
@Injectable({ providedIn: 'root' })
export class NetworkStatusService {
  private readonly _online = signal(typeof navigator === 'undefined' ? true : navigator.onLine);
  readonly online = this._online.asReadonly();

  constructor() {
    if (typeof window === 'undefined') return;
    const update = () => this._online.set(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    inject(DestroyRef).onDestroy(() => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    });
  }
}
