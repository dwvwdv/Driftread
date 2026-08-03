import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import {
  apiMessage,
  isMissingApiKeyHeader,
  isRateLimited,
  retryAfterSeconds,
  validationMessage,
} from './http-errors';

const err = (init: { status: number; error?: unknown; headers?: Record<string, string> }) =>
  new HttpErrorResponse({
    status: init.status,
    error: init.error,
    headers: new HttpHeaders(init.headers ?? {}),
  });

describe('isMissingApiKeyHeader', () => {
  // FastAPI answers 422 for a missing required Header(...) and for any schema
  // violation alike. Telling them apart is what stops a malformed JSON import
  // from clearing a perfectly valid admin key.
  const missingHeader = {
    detail: [{ type: 'missing', loc: ['header', 'x-api-key'], msg: 'Field required' }],
  };

  const badBody = {
    detail: [{ type: 'missing', loc: ['body', 'feeds', 0, 'title'], msg: 'Field required' }],
  };

  it('recognises a missing X-API-Key header', () => {
    expect(isMissingApiKeyHeader(err({ status: 422, error: missingHeader }))).toBe(true);
  });

  it('does NOT treat a schema-invalid request body as an auth failure', () => {
    expect(isMissingApiKeyHeader(err({ status: 422, error: badBody }))).toBe(false);
  });

  it('matches the header name case-insensitively', () => {
    const upper = { detail: [{ loc: ['header', 'X-API-Key'], msg: 'Field required' }] };
    expect(isMissingApiKeyHeader(err({ status: 422, error: upper }))).toBe(true);
  });

  it('ignores non-422 statuses even if the body mentions the header', () => {
    expect(isMissingApiKeyHeader(err({ status: 403, error: missingHeader }))).toBe(false);
  });

  it('survives a 422 with an unexpected body shape', () => {
    expect(isMissingApiKeyHeader(err({ status: 422, error: null }))).toBe(false);
    expect(isMissingApiKeyHeader(err({ status: 422, error: { detail: 'plain string' } }))).toBe(
      false,
    );
    expect(isMissingApiKeyHeader(err({ status: 422, error: { detail: [{}] } }))).toBe(false);
  });
});

describe('validationMessage', () => {
  it('names the offending field without the leading body/query segment', () => {
    const error = err({
      status: 422,
      error: { detail: [{ loc: ['body', 'feeds', 0, 'title'], msg: 'Field required' }] },
    });
    expect(validationMessage(error)).toBe('feeds.0.title：Field required');
  });

  it('returns empty for a non-validation error', () => {
    expect(validationMessage(err({ status: 500, error: { detail: 'boom' } }))).toBe('');
  });
});

describe('apiMessage', () => {
  it('separates "never reached the server" from a server-side failure', () => {
    expect(apiMessage(err({ status: 0 }))).toBe('無法連線到後端服務');
  });

  it('prefers the backend detail string', () => {
    expect(apiMessage(err({ status: 502, error: { detail: 'Failed to fetch feed' } }))).toBe(
      'Failed to fetch feed',
    );
  });

  it('falls back to the validation summary rather than HttpClient boilerplate', () => {
    const error = err({
      status: 422,
      error: { detail: [{ loc: ['body', 'url'], msg: 'Input should be a valid URL' }] },
    });
    expect(apiMessage(error)).toBe('url：Input should be a valid URL');
  });
});

describe('rate limiting', () => {
  it('detects 429', () => {
    expect(isRateLimited(err({ status: 429 }))).toBe(true);
    expect(isRateLimited(err({ status: 500 }))).toBe(false);
  });

  it('reads Retry-After', () => {
    expect(retryAfterSeconds(err({ status: 429, headers: { 'Retry-After': '42' } }))).toBe(42);
  });

  it('falls back when Retry-After is absent or nonsense', () => {
    expect(retryAfterSeconds(err({ status: 429 }), 30)).toBe(30);
    expect(retryAfterSeconds(err({ status: 429, headers: { 'Retry-After': 'soon' } }), 30)).toBe(
      30,
    );
  });
});
