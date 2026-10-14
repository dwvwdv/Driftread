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
import { firstValueFrom } from 'rxjs';
import {
  ReviewOAuthGrant,
  reviewOAuthError,
  ReviewOAuthService,
} from '../../../services/review-oauth';
import { ObCard } from '../../../ui/card/card';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObEmpty, ObError, ObLoading } from '../../../ui/state/state';

@Component({
  selector: 'app-admin-mcp-connections',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, ObCard, ObPageHeader, ObEmpty, ObError, ObLoading],
  templateUrl: './admin-mcp-connections.html',
  styleUrl: './admin-mcp-connections.scss',
})
export class AdminMcpConnections implements OnInit, OnDestroy {
  private oauth = inject(ReviewOAuthService);
  private destroyRef = inject(DestroyRef);
  protected grants = signal<ReviewOAuthGrant[]>([]);
  protected loading = signal(true);
  protected revoking = signal<string | null>(null);
  protected error = signal('');
  private generation = 0;
  private destroyed = false;

  ngOnInit(): void {
    void this.load();
  }

  protected async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading.set(true);
    this.error.set('');
    try {
      const grants = await firstValueFrom(
        this.oauth.grants().pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (generation === this.generation && !this.destroyed) this.grants.set(grants);
    } catch (error) {
      if (generation === this.generation && !this.destroyed)
        this.error.set(reviewOAuthError(error));
    } finally {
      if (generation === this.generation && !this.destroyed) this.loading.set(false);
    }
  }

  protected async revoke(id: string): Promise<void> {
    const grant = this.grants().find((grant) => grant.id === id);
    if (this.revoking() || this.destroyed || !grant || this.inactive(grant)) return;
    ++this.generation; // Fence older GET responses before the write starts.
    this.loading.set(false);
    this.revoking.set(id);
    this.error.set('');
    try {
      await firstValueFrom(this.oauth.revoke(id).pipe(takeUntilDestroyed(this.destroyRef)));
      if (this.destroyed) return;
      this.grants.update((grants) =>
        grants.map((grant) =>
          grant.id === id ? { ...grant, revoked_at: new Date().toISOString() } : grant,
        ),
      );
      await this.load();
    } catch (error) {
      if (!this.destroyed) this.error.set(reviewOAuthError(error));
    } finally {
      if (!this.destroyed) this.revoking.set(null);
    }
  }

  protected inactive(grant: ReviewOAuthGrant): boolean {
    return !!grant.revoked_at || !(Date.parse(grant.expires_at) > Date.now());
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    ++this.generation;
  }
}
