import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth';
import { ObCallout } from '../../ui/callout/callout';

/**
 * Sign in / sign up.
 *
 * Two fixes beyond the restyle:
 *
 *  - Wrapped in a <form (ngSubmit)>. Previously only the click handler ran, so
 *    pressing Enter in the password field did nothing — on a login form, of all
 *    places.
 *
 *  - When Supabase is unconfigured the form is now disabled, not merely
 *    accompanied by a warning. It used to stay fully interactive and fail
 *    silently, which reads as a broken site rather than a missing build-time
 *    setting.
 */
@Component({
  selector: 'app-login',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCallout],
  templateUrl: './login.html',
  styleUrl: './login.scss',
})
export class Login {
  protected auth = inject(AuthService);
  private router = inject(Router);

  mode = signal<'login' | 'signup'>('login');
  email = '';
  password = '';
  error = signal('');
  info = signal('');
  busy = signal(false);

  setMode(mode: 'login' | 'signup'): void {
    if (mode === this.mode()) return;
    this.mode.set(mode);
    this.error.set('');
    this.info.set('');
  }

  async submit(): Promise<void> {
    if (!this.auth.isConfigured()) return;

    this.error.set('');
    this.info.set('');

    if (!this.email || !this.password) {
      this.error.set('請輸入 Email 與密碼');
      return;
    }

    this.busy.set(true);
    const result =
      this.mode() === 'login'
        ? await this.auth.signIn(this.email, this.password)
        : await this.auth.signUp(this.email, this.password);
    this.busy.set(false);

    if (result.error) {
      this.error.set(result.error);
      return;
    }

    if (this.mode() === 'signup') {
      this.info.set('註冊成功，請檢查信箱完成驗證後再登入。');
      return;
    }

    void this.router.navigateByUrl('/');
  }
}
