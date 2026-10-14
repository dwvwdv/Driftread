import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, defer, Observable, tap, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { ManageSessionService } from './manage-session';

export interface ReviewOAuthConsent {
  request_id: string;
  client_name: string;
  redirect_host: string;
  scope: string;
  expires_at: string;
}

export interface ReviewOAuthGrant {
  id: string;
  client_name: string;
  scope: string;
  created_at: string;
  expires_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

/** Only navigate to the same ChatGPT callback destinations allowed by the server. */
export function isReviewOAuthRedirect(value: string): boolean {
  try {
    // URL normalizes an explicit :443 away; the raw authority must still have no port.
    if (!/^https:\/\/chatgpt\.com\//.test(value)) return false;
    const url = new URL(value);
    return (
      url.protocol === 'https:' &&
      url.hostname === 'chatgpt.com' &&
      !url.port &&
      !url.username &&
      !url.password &&
      !url.hash &&
      (url.pathname === '/connector_platform_oauth_redirect' ||
        /^\/connector\/oauth\/[A-Za-z0-9_-]+$/.test(url.pathname))
    );
  } catch {
    return false;
  }
}

export function reviewOAuthError(error: unknown): string {
  if (!(error instanceof HttpErrorResponse)) {
    return error instanceof Error ? error.message : '操作失敗，請稍後再試';
  }
  switch (error.status) {
    case 0:
      return '無法連線到後端服務';
    case 401:
      return '後台驗證已失效，請重新登入';
    case 403:
      return '此帳號無權管理這項授權';
    case 400:
    case 404:
    case 409:
    case 410:
    case 422:
      return '授權請求無效、已使用或已過期，請回 ChatGPT 重新連線';
    case 429:
      return '請求過於頻繁，請稍後再試';
    case 503:
      return 'OAuth 服務尚未啟用或暫時無法使用';
    default:
      return '操作失敗，請稍後再試';
  }
}

@Injectable({ providedIn: 'root' })
export class ReviewOAuthService {
  private http = inject(HttpClient);
  private sessions = inject(ManageSessionService);
  private router = inject(Router);
  private base = `${environment.apiUrl}/review-oauth`;

  consent(id: string): Observable<ReviewOAuthConsent> {
    return this.request((headers) =>
      this.http.get<ReviewOAuthConsent>(`${this.base}/consent/${encodeURIComponent(id)}`, {
        headers,
      }),
    );
  }

  decide(id: string, approved: boolean): Observable<{ redirect_url: string }> {
    return this.request((headers) =>
      this.http.post<{ redirect_url: string }>(
        `${this.base}/consent/${encodeURIComponent(id)}`,
        { approved },
        { headers },
      ),
    );
  }

  grants(): Observable<ReviewOAuthGrant[]> {
    return this.request((headers) =>
      this.http.get<ReviewOAuthGrant[]>(`${this.base}/grants`, { headers }),
    );
  }

  revoke(id: string): Observable<void> {
    return this.request((headers) =>
      this.http.delete<void>(`${this.base}/grants/${encodeURIComponent(id)}`, { headers }),
    );
  }

  private request<T>(send: (headers: Record<string, string>) => Observable<T>): Observable<T> {
    return defer(() => {
      const identity = this.sessions.identity();
      const headers = this.sessions.headers();
      const current = () => {
        const currentIdentity = this.sessions.identity();
        const now = this.sessions.headers();
        return (
          identity !== null &&
          currentIdentity?.userId === identity.userId &&
          currentIdentity.authSessionId === identity.authSessionId &&
          now['x-manage-session'] === headers['x-manage-session']
        );
      };
      return send(headers).pipe(
        tap(() => {
          if (!current()) throw new Error('登入帳號已變更，請重新登入');
        }),
        catchError((error: unknown) => {
          // A refreshed JWT still belongs to the same login, but its predecessor's
          // delayed 401 must not clear the refreshed credentials.
          if (
            current() &&
            this.sessions.headers()['Authorization'] === headers['Authorization'] &&
            error instanceof HttpErrorResponse &&
            error.status === 401
          ) {
            this.sessions.clear();
            void this.router.navigate(['/manage/login'], {
              queryParams: { redirect: this.router.url },
            });
          }
          return throwError(() => error);
        }),
      );
    });
  }
}
