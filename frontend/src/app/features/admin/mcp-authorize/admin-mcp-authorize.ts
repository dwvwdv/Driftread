import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  OnDestroy,
  OnInit,
  signal,
} from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import {
  isReviewOAuthRedirect,
  ReviewOAuthConsent,
  reviewOAuthError,
  ReviewOAuthService,
} from '../../../services/review-oauth';
import { ObCard } from '../../../ui/card/card';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObError, ObLoading } from '../../../ui/state/state';

@Component({
  selector: 'app-admin-mcp-authorize',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, RouterLink, ObCard, ObPageHeader, ObError, ObLoading],
  templateUrl: './admin-mcp-authorize.html',
  styleUrl: './admin-mcp-authorize.scss',
})
export class AdminMcpAuthorize implements OnInit, OnDestroy {
  private oauth = inject(ReviewOAuthService);
  private destroyRef = inject(DestroyRef);
  private route = inject(ActivatedRoute);
  protected consent = signal<ReviewOAuthConsent | null>(null);
  protected loading = signal(true);
  protected busy = signal(false);
  protected error = signal('');
  private request = '';
  private destroyed = false;

  ngOnInit(): void {
    this.request = this.route.snapshot.queryParamMap.get('request') ?? '';
    void this.load();
  }

  private async load(): Promise<void> {
    if (!/^[A-Za-z0-9_-]{16,128}$/.test(this.request)) {
      this.error.set('授權請求無效，請回 ChatGPT 重新連線');
      this.loading.set(false);
      return;
    }
    try {
      const consent = await firstValueFrom(
        this.oauth.consent(this.request).pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (this.destroyed) return;
      if (
        consent.request_id !== this.request ||
        consent.redirect_host !== 'chatgpt.com' ||
        !this.validScope(consent.scope) ||
        !(Date.parse(consent.expires_at) > Date.now())
      ) {
        throw new Error('授權請求無效或已過期，請回 ChatGPT 重新連線');
      }
      this.consent.set(consent);
    } catch (error) {
      if (!this.destroyed) this.error.set(reviewOAuthError(error));
    } finally {
      if (!this.destroyed) this.loading.set(false);
    }
  }

  protected async decide(approved: boolean): Promise<void> {
    if (this.busy() || !this.consent() || this.destroyed) return;
    if (!(Date.parse(this.consent()!.expires_at) > Date.now())) {
      this.consent.set(null);
      this.error.set('授權請求已過期，請回 ChatGPT 重新連線');
      return;
    }
    this.busy.set(true);
    this.error.set('');
    try {
      const result = await firstValueFrom(
        this.oauth.decide(this.request, approved).pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (this.destroyed) return;
      this.consent.set(null);
      if (!isReviewOAuthRedirect(result.redirect_url)) {
        throw new Error('授權回跳網址不符合安全限制，請回 ChatGPT 重新連線');
      }
      // Preserve the server's code/state/error without rebuilding the callback.
      this.navigate(result.redirect_url);
    } catch (error) {
      if (!this.destroyed) this.error.set(reviewOAuthError(error));
    } finally {
      if (!this.destroyed) this.busy.set(false);
    }
  }

  private validScope(scope: string): boolean {
    const scopes = scope.split(' ');
    return (
      scopes.includes('source:review') &&
      scopes.every((value) => value === 'source:review' || value === 'offline_access')
    );
  }

  protected navigate(url: string): void {
    window.location.assign(url);
  }
  ngOnDestroy(): void {
    this.destroyed = true;
  }
}
