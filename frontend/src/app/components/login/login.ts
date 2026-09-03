import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth';
import { DiscoverService } from '../../services/discover';
import { SubscriptionService } from '../../services/subscription';
import { apiMessage } from '../../shared/http-errors';
import { takePendingImportFeedUrl } from '../../shared/pending-import';
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
 *  - Also resumes a pending feed import stashed by Discover.importFeed() (see
 *    shared/pending-import.ts) via sessionStorage — deliberately not a query
 *    param the way `subscribeFeed` is: POST /discover/import now requires a
 *    signed-in caller (docs/SECURITY.md #30) specifically to stop arbitrary
 *    URLs being fetched and written to the global catalog on request alone,
 *    and a query param read here would just move that same hole to a crafted
 *    `/login?importFeedUrl=...` link acted on by a login this page did not
 *    initiate (Codex review on PR #52). sessionStorage can only be written by
 *    this app's own JS running on Discover after an actual click — and even
 *    then only resumed when `importNonce` matches the one that stash handed
 *    out, so backing out of /login and later signing in for something
 *    unrelated doesn't silently resume an abandoned import.
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

    // Only resume when this login was actually reached via that redirect
    // (importNonce present and matching) — not on any other login that
    // happens to follow an abandoned import click in the same tab (third-
    // round Codex review on PR #52).
    const importNonce = this.route.snapshot.queryParamMap.get('importNonce');
    const importFeedUrl = importNonce ? takePendingImportFeedUrl(importNonce) : null;
    if (importFeedUrl) {
      // Resumes the import Discover.importFeed() deferred for a signed-out
      // click, instead of dropping the reader back on a blank /discover form
      // (Codex review on PR #52). Fire-and-forget like the subscribe branch
      // above — this page navigates away immediately either way, so there's
      // nowhere here to await a result other than a toast.
      //
      // requestedFor guards against a sign-out/account-switch landing between
      // firing this request and its response — same class of bug, same fix,
      // as Discover.importFeed()'s own guard (Codex review on PR #52):
      // without it, a slow response would credit whoever is signed in *now*
      // with a subscription the backend actually created for whoever had
      // just signed in when this request was sent.
      const requestedFor = this.auth.session()?.user?.id ?? null;
      this.discover.importByUrl(importFeedUrl).subscribe({
        next: (feed) => {
          if (requestedFor && (this.auth.session()?.user?.id ?? null) === requestedFor) {
            this.subs.markSubscribed(feed.id);
          }
        },
        error: (err: unknown) => this.toast.danger(apiMessage(err, '匯入失敗')),
      });
    }

    const redirect = this.route.snapshot.queryParamMap.get('redirect') || '/';
    void this.router.navigateByUrl(redirect);
  }
}
