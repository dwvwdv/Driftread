import { ChangeDetectionStrategy, Component, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../../services/auth';
import { ManageSessionService, OtpChallenge } from '../../../services/manage-session';
import { apiMessage, retryAfterSeconds } from '../../../shared/http-errors';
import { HttpErrorResponse } from '@angular/common/http';
import { ObIcon } from '../../../ui/icon/icon';

@Component({
  selector: 'app-admin-unlock',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, RouterLink, ObIcon],
  templateUrl: './admin-unlock.html',
  styleUrl: './admin-unlock.scss',
})
export class AdminUnlock implements OnDestroy {
  protected auth = inject(AuthService);
  private sessions = inject(ManageSessionService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  protected email = '';
  protected password = '';
  protected code = '';
  protected error = signal('');
  protected busy = signal(false);
  protected challenge = signal<OtpChallenge | null>(null);
  protected cooldown = signal(0);
  private timer: ReturnType<typeof setInterval> | null = null;
  private destroyed = false;

  protected async submit(): Promise<void> {
    if (this.busy()) return;
    this.error.set('');
    this.busy.set(true);
    try {
      const challenge = this.challenge();
      if (challenge) {
        if (!/^[0-9]{6}$/.test(this.code)) throw new Error('請輸入 6 位數驗證碼');
        await this.sessions.verify(challenge.challenge_id, this.code);
        if (this.destroyed) return;
        this.code = '';
        const redirect = this.route.snapshot.queryParamMap.get('redirect');
        const safe =
          redirect && /^\/manage(?:\/|$)/.test(redirect) && !redirect.startsWith('/manage/login')
            ? redirect
            : '/manage/dashboard';
        await this.router.navigateByUrl(safe);
      } else {
        if (!this.email || !this.password) throw new Error('請輸入 Email 與密碼');
        this.sessions.clear();
        const result = await this.auth.signIn(this.email.trim(), this.password);
        this.password = '';
        if (result.error) throw new Error(result.error);
        if (this.destroyed) return;
        await this.send();
      }
    } catch (error) {
      this.handleError(error);
    } finally {
      this.busy.set(false);
    }
  }

  protected async resend(): Promise<void> {
    if (this.busy() || this.cooldown() > 0) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.send();
    } catch (error) {
      this.handleError(error);
    } finally {
      this.busy.set(false);
    }
  }

  protected restart(): void {
    if (this.busy()) return;
    this.challenge.set(null);
    this.password = '';
    this.code = '';
    this.error.set('');
  }

  private async send(): Promise<void> {
    const challenge = await this.sessions.request();
    if (this.destroyed) return;
    this.challenge.set(challenge);
    this.code = '';
    this.startCooldown(challenge.retry_after);
  }

  private handleError(error: unknown): void {
    if (this.destroyed) return;
    this.error.set(apiMessage(error, '後台登入失敗'));
    if (error instanceof HttpErrorResponse && error.status === 429) {
      this.startCooldown(retryAfterSeconds(error, 60));
    }
    if (error instanceof HttpErrorResponse && error.status === 401) {
      this.challenge.set(null);
      this.code = '';
    }
  }

  private startCooldown(seconds: number): void {
    if (this.timer) clearInterval(this.timer);
    const deadline = Date.now() + seconds * 1000;
    this.cooldown.set(seconds);
    this.timer = setInterval(() => {
      this.cooldown.set(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
      if (this.cooldown() === 0 && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    }, 1000);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.password = '';
    this.code = '';
    if (this.timer) clearInterval(this.timer);
  }
}
