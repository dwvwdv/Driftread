import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  inject,
  OnDestroy,
  OnInit,
  signal,
} from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { FormsModule } from '@angular/forms';
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
  imports: [DatePipe, FormsModule, ObCard, ObPageHeader, ObEmpty, ObError, ObLoading],
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
  protected saving = signal<string | null>(null);
  protected writing = computed(() => this.saving() !== null || this.revoking() !== null);
  protected editing = signal<string | null>(null);
  protected draftError = signal('');
  protected permanent = false;
  protected expiry = '';
  protected readonly timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  private generation = 0;
  private destroyed = false;

  ngOnInit(): void {
    void this.load();
  }

  protected async load(quiet = false): Promise<void> {
    const generation = ++this.generation;
    if (!quiet) this.loading.set(true);
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
    if (this.writing() || this.destroyed || !grant || this.inactive(grant)) return;
    ++this.generation; // Fence older GET responses before the write starts.
    this.loading.set(false);
    this.revoking.set(id);
    this.error.set('');
    try {
      await firstValueFrom(this.oauth.revoke(id).pipe(takeUntilDestroyed(this.destroyRef)));
      if (this.destroyed) return;
      ++this.generation;
      if (this.editing() === id) this.cancelEdit();
      this.grants.update((grants) =>
        grants.map((grant) =>
          grant.id === id ? { ...grant, revoked_at: new Date().toISOString() } : grant,
        ),
      );
      await this.load(true);
    } catch (error) {
      if (!this.destroyed) this.error.set(reviewOAuthError(error));
    } finally {
      if (!this.destroyed) this.revoking.set(null);
    }
  }

  protected edit(grant: ReviewOAuthGrant): void {
    if (this.writing() || this.destroyed || this.inactive(grant)) return;
    this.editing.set(grant.id);
    this.permanent = grant.expires_at === null;
    this.expiry = grant.expires_at === null ? '' : this.localDateTime(new Date(grant.expires_at));
    this.draftError.set('');
  }

  protected cancelEdit(): void {
    if (this.saving() !== null) return;
    this.editing.set(null);
    this.draftError.set('');
  }

  protected async saveExpiry(id: string): Promise<void> {
    const grant = this.grants().find((grant) => grant.id === id);
    if (this.writing() || this.destroyed || this.editing() !== id || !grant || this.inactive(grant))
      return;
    let expiresAt: string | null = null;
    if (!this.permanent) {
      const input = this.expiry;
      const date = new Date(input);
      const normalized = input.length === 16 ? `${input}:00` : input;
      if (
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(input) ||
        !Number.isFinite(date.getTime()) ||
        this.localDateTime(date) !== normalized ||
        date.getTime() <= Date.now()
      ) {
        this.draftError.set('請輸入有效且晚於目前時間的日期與時間。');
        return;
      }
      expiresAt = date.toISOString();
    }
    ++this.generation;
    this.loading.set(false);
    this.saving.set(id);
    this.error.set('');
    this.draftError.set('');
    try {
      const updated = await firstValueFrom(
        this.oauth.updateExpiry(id, expiresAt).pipe(takeUntilDestroyed(this.destroyRef)),
      );
      if (this.destroyed) return;
      ++this.generation;
      this.grants.update((grants) => grants.map((grant) => (grant.id === id ? updated : grant)));
      this.editing.set(null);
      await this.load(true);
    } catch (error) {
      if (this.destroyed) return;
      if (error instanceof HttpErrorResponse && error.status === 422) {
        this.draftError.set('請選擇晚於目前時間的有效日期與時間。');
      } else if (error instanceof HttpErrorResponse && [404, 409, 410].includes(error.status)) {
        await this.load(true);
        if (!this.destroyed)
          this.error.set('無法更新有效期限，此連線可能已過期或撤銷。已重新確認授權狀態。');
      } else {
        this.error.set(reviewOAuthError(error));
      }
    } finally {
      if (!this.destroyed) this.saving.set(null);
    }
  }

  private localDateTime(date: Date): string {
    const pad = (value: number) => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  protected inactive(grant: ReviewOAuthGrant): boolean {
    return (
      !!grant.revoked_at ||
      (grant.expires_at !== null && !(Date.parse(grant.expires_at) > Date.now()))
    );
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    ++this.generation;
  }
}
