import { HttpErrorResponse } from '@angular/common/http';

/**
 * One entry from a FastAPI/Pydantic validation failure.
 *
 * `loc` is the path to the offending value, e.g. `['header', 'x-api-key']` for a
 * missing header or `['body', 'feeds', 0, 'title']` for a bad request body.
 */
export interface ValidationDetail {
  type?: string;
  loc?: (string | number)[];
  msg?: string;
}

/**
 * FastAPI's error body. `detail` is a plain string for HTTPException, but an
 * array of ValidationDetail for a 422. Typed rather than `any` so reading it
 * survives strict mode.
 */
export interface ApiError {
  detail?: string | ValidationDetail[];
}

function validationDetails(error: unknown): ValidationDetail[] {
  if (!(error instanceof HttpErrorResponse)) return [];
  const body = error.error as ApiError | null;
  return body && Array.isArray(body.detail) ? body.detail : [];
}

/**
 * True only when a 422 is specifically about the missing X-API-Key header.
 *
 * This distinction matters: FastAPI returns 422 for *any* schema violation, so a
 * feed import whose JSON is missing a `title` produces the same status as a
 * request with no key header at all. Treating both the same would clear a
 * perfectly good key and bounce the operator to the unlock screen because they
 * pasted malformed JSON.
 */
export function isMissingApiKeyHeader(error: unknown): boolean {
  if (!(error instanceof HttpErrorResponse) || error.status !== 422) return false;

  return validationDetails(error).some((detail) => {
    const loc = (detail.loc ?? []).map((part) => String(part).toLowerCase());
    return loc.includes('header') && loc.includes('x-api-key');
  });
}

/** Renders a validation failure as something an operator can act on. */
export function validationMessage(error: unknown): string {
  const details = validationDetails(error);
  if (!details.length) return '';

  return details
    .slice(0, 3)
    .map((detail) => {
      // Drop the leading 'body'/'query' segment — it is noise for a reader.
      const field = (detail.loc ?? []).slice(1).join('.');
      return field ? `${field}：${detail.msg ?? '格式錯誤'}` : (detail.msg ?? '格式錯誤');
    })
    .join('；');
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

  // 422 bodies carry an array rather than a string; without this the caller would
  // fall through to HttpClient's generic "Http failure response for …".
  const validation = validationMessage(error);
  if (validation) return validation;

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
