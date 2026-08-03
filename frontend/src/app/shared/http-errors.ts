import { HttpErrorResponse } from '@angular/common/http';

/**
 * FastAPI's error body shape. Typed rather than `any` so that reading `.detail`
 * off it survives strict mode.
 */
export interface ApiError {
  detail?: string;
}

/**
 * Best-effort human-readable message for a failed request.
 *
 * The backend deliberately never returns upstream exception text (docs/SECURITY.md),
 * so `detail` is a short, safe string when present. Status 0 is the case worth
 * separating: it means the request never reached the server at all.
 */
export function apiMessage(error: unknown, fallback = '請求失敗'): string {
  if (!(error instanceof HttpErrorResponse)) {
    return error instanceof Error ? error.message : fallback;
  }

  if (error.status === 0) return '無法連線到後端服務';

  const body = error.error as ApiError | string | null;
  if (typeof body === 'string' && body.trim()) return body;
  if (body && typeof body === 'object' && typeof body.detail === 'string') return body.detail;

  return error.message || fallback;
}

/**
 * Seconds to wait after a 429, from the Retry-After header the rate limiter sets
 * (backend/rate_limit.py).
 *
 * Same-origin in production — nginx proxies /api/ — so the header is readable
 * without any Access-Control-Expose-Headers dance.
 */
export function retryAfterSeconds(error: unknown, fallback = 30): number {
  if (!(error instanceof HttpErrorResponse)) return fallback;
  const header = error.headers.get('Retry-After');
  const seconds = header ? Number(header) : NaN;
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : fallback;
}

export function isRateLimited(error: unknown): boolean {
  return error instanceof HttpErrorResponse && error.status === 429;
}
