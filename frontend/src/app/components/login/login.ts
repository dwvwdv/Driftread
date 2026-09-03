import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth';
import { DiscoverService } from '../../services/discover';
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
 *
 *  - Also honors `importFeedUrl` (see Discover.importFeed): a reader who
 *    pasted a URL, found a not-yet-catalogued candidate and clicked import
 *    while signed out is sent here instead of straight to the backend
 *    (POST /discover/import now requires a signed-in caller — see
 *    docs/SECURITY.md #30). Without resuming it here, they'd land back on a
 *    blank /discover form and have to re-paste the URL, re-run discovery and
 *    click import again — the same loss `subscribeFeed` already avoids for
 *    an existing feed's subscribe button.
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
  private discover = inject(DiscoverService);
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

  get pendingImportFeedUrl(): string | null {
    return this.route.snapshot.queryParamMap.get('importFeedUrl');
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

    const importFeedUrl = this.pendingImportFeedUrl;
    if (importFeedUrl) {
      // Resumes the import Discover.importFeed() deferred for a signed-out
      // click, instead of dropping the reader back on a blank /discover form
      // (Codex review on PR #52). Fire-and-forget like the subscribe branch
      // above — this page navigates away immediately either way, so there's
      // nowhere here to await a result other than a toast.
      this.discover.importByUrl(importFeedUrl).subscribe({
        next: (feed) => this.subs.markSubscribed(feed.id),
        error: (err: unknown) => this.toast.danger(apiMessage(err, '匯入失敗')),
      });
    }

    const redirect = this.route.snapshot.queryParamMap.get('redirect') || '/';
    void this.router.navigateByUrl(redirect);
  }
}
