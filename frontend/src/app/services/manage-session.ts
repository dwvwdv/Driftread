import { computed, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth';

const STORAGE_KEY = 'driftread_manage_session';
interface ManagementSession {
  token: string;
  expires_at: string;
  userId: string;
  authSessionId: string;
}
export interface OtpChallenge {
  challenge_id: string;
  expires_in: number;
  retry_after: number;
}

@Injectable({ providedIn: 'root' })
export class ManageSessionService {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private saved = signal<ManagementSession | null>(this.read());
  private base = `${environment.apiUrl}/manage/auth`;
  private identity = computed(() => {
    const session = this.auth.session();
    if (!session) return null;
    try {
      const payload = JSON.parse(
        atob(session.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')),
      );
      return { userId: session.user.id, authSessionId: payload.session_id as string };
    } catch {
      return null;
    }
  });

  token(): string | null {
    const saved = this.saved();
    const identity = this.identity();
    return saved &&
      identity &&
      saved.userId === identity.userId &&
      saved.authSessionId === identity.authSessionId &&
      Date.parse(saved.expires_at) > Date.now()
      ? saved.token
      : null;
  }

  headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.auth.accessToken ?? ''}`,
      'x-manage-session': this.token() ?? '',
    };
  }

  async request(): Promise<OtpChallenge> {
    return firstValueFrom(
      this.http.post<OtpChallenge>(`${this.base}/request`, {}, { headers: this.headers() }),
    );
  }

  async verify(challenge: string, code: string): Promise<void> {
    const identity = this.identity();
    if (!identity) throw new Error('請先登入');
    const result = await firstValueFrom(
      this.http.post<{ token: string; expires_at: string }>(
        `${this.base}/verify`,
        { challenge_id: challenge, code },
        { headers: this.headers() },
      ),
    );
    const current = this.identity();
    if (
      !current ||
      current.userId !== identity.userId ||
      current.authSessionId !== identity.authSessionId
    ) {
      throw new Error('登入帳號已變更，請重新登入');
    }
    const session = { ...result, ...identity };
    this.saved.set(session);
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch {
      /* Memory only. */
    }
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(this.http.post(`${this.base}/logout`, {}, { headers: this.headers() }));
    } finally {
      this.clear();
    }
  }

  clear(): void {
    this.saved.set(null);
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* Unavailable storage. */
    }
  }

  private read(): ManagementSession | null {
    try {
      // Remove the old shared API key when upgrading an existing tab.
      sessionStorage.removeItem('driftread_admin_key');
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null');
      return saved &&
        typeof saved.token === 'string' &&
        typeof saved.expires_at === 'string' &&
        typeof saved.userId === 'string' &&
        typeof saved.authSessionId === 'string'
        ? saved
        : null;
    } catch {
      return null;
    }
  }
}
