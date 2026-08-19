import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth';
import { SubscriptionService } from '../../services/subscription';
import { apiMessage } from '../../shared/http-errors';
import { ObCallout } from '../../ui/callout/callout';
import { ToastService } from '../../ui/toast/toast';

/**
 * Sign in / sign up.
 *
 * Three fixes beyond the restyle:
 *
 *  - Wrapped in a <form (ngSubmit)>. Previously only the click handler ran, so
 *    pressing Enter in the password field did nothing — on a login form, of all
 *    places.
 *
 *  - When Supabase is unconfigured the form is now disabled, not merely
 *    accompanied by a warning. It used to stay fully interactive and fail
 *    silently, which reads as a broken site rather than a missing build-time
 *    setting.
 *
 *  - Honors `redirect` / `subscribeFeed` query params: a reader who clicked
 *    "訂閱" while signed out is sent here with both set (see
 *    FeedDetail.toggleSubscribe, FeedList.quickSubscribe, Discover.subscribeExisting),
 *    and lands back on that exact feed with the subscription already done,
 *    instead of on the home page having to find it and click subscribe again.
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
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private subs = inject(SubscriptionService);
  private toast = inject(ToastService);

  mode = signal<'login' | 'signup'>('login');
  email = '';
  password = '';
  error = signal('');
  info = signal('');
  busy = signal(false);

  get pendingSubscribeFeed(): string | null {
    return this.route.snapshot.queryParamMap.get('subscribeFeed');
  }

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

    const feedId = this.pendingSubscribeFeed;
    if (feedId) {
      // SubscriptionService's own identity effect is scheduled, not
      // synchronous with the session signal write auth.signIn() just
      // caused — without forcing it to catch up first, subscribe() below
      // could still tag this write with the pre-login identity, and its
      // response would then get silently dropped by subscribe()'s own
      // requestedFor guard once the effect does catch up.
      this.subs.syncIdentity();
      this.subs.subscribe(feedId, (err) => this.toast.danger(apiMessage(err, '訂閱失敗')));
    }

    const redirect = this.route.snapshot.queryParamMap.get('redirect') || '/';
    void this.router.navigateByUrl(redirect);
  }
}
