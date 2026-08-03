import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../services/me';
import { AuthService } from '../../services/auth';
import { Feed, OpmlImportResult } from '../../models';
import { apiMessage } from '../../shared/http-errors';
import { ObCallout } from '../../ui/callout/callout';
import { ObIcon } from '../../ui/icon/icon';
import { ObLoading, ObEmpty } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ToastService } from '../../ui/toast/toast';

/** Subscriptions, plus OPML interchange with other readers. */
@Component({
  selector: 'app-my-feeds',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, ObCallout, ObIcon, ObLoading, ObEmpty, ObPageHeader],
  templateUrl: './my-feeds.html',
  styleUrl: './my-feeds.scss',
})
export class MyFeeds {
  protected auth = inject(AuthService);
  private me = inject(MeService);
  private toast = inject(ToastService);

  feeds = signal<Feed[]>([]);
  loading = signal(false);
  importResult = signal<OpmlImportResult | null>(null);
  showFailures = signal(false);
  exporting = signal(false);
  importing = signal(false);

  /** User id the subscriptions have already been loaded for. */
  private loadedFor: string | null = null;

  constructor() {
    // Not `if (session()) load()` in ngOnInit: AuthService restores the persisted
    // session asynchronously, so on a direct visit that check runs before the
    // session exists and never runs again. The template would then flip from
    // "please sign in" to an empty subscription list once the session landed.
    //
    // Keyed on the user id so signing out and back in — or switching accounts —
    // reloads rather than showing the previous user's list.
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.load();
    });
  }

  load(): void {
    this.loading.set(true);
    this.me.listSubscriptions().subscribe({
      next: (feeds) => {
        this.feeds.set(feeds);
        this.loading.set(false);
      },
      error: (e: unknown) => {
        this.loading.set(false);
        this.toast.danger(apiMessage(e, '讀取訂閱失敗'));
      },
    });
  }

  unsubscribe(feed: Feed): void {
    this.me.unsubscribe(feed.id).subscribe({
      next: () => {
        this.toast.info(`已取消訂閱：${feed.title}`);
        this.feeds.update((list) => list.filter((f) => f.id !== feed.id));
      },
      error: (e: unknown) => this.toast.danger(apiMessage(e, '取消訂閱失敗')),
    });
  }

  onOpml(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    this.importing.set(true);
    this.showFailures.set(false);
    this.me.importOpml(file).subscribe({
      next: (result) => {
        this.importing.set(false);
        this.importResult.set(result);
        this.load();
      },
      error: (e: unknown) => {
        this.importing.set(false);
        this.toast.danger(apiMessage(e, 'OPML 匯入失敗'));
      },
    });

    // Lets the same file be picked again after a failed attempt; without this the
    // change event never fires a second time for an identical selection.
    input.value = '';
  }

  /**
   * Fetches the OPML through HttpClient so the auth interceptor attaches the
   * token, then saves it. See MeService.exportOpml — the old <a href> always 401'd.
   */
  exportOpml(): void {
    this.exporting.set(true);
    this.me.exportOpml().subscribe({
      next: (blob) => {
        this.exporting.set(false);
        const url = URL.createObjectURL(blob);
        try {
          const link = document.createElement('a');
          link.href = url;
          link.download = 'driftread.opml';
          link.click();
        } finally {
          // Released either way; a retained object URL pins the blob in memory.
          URL.revokeObjectURL(url);
        }
      },
      error: (e: unknown) => {
        this.exporting.set(false);
        this.toast.danger(apiMessage(e, 'OPML 匯出失敗'));
      },
    });
  }

  toggleFailures(): void {
    this.showFailures.update((open) => !open);
  }

  async copyFailures(): Promise<void> {
    const failed = this.importResult()?.failed ?? [];
    try {
      await navigator.clipboard.writeText(failed.join('\n'));
      this.toast.success('已複製失敗清單');
    } catch {
      // Clipboard access needs a secure context and can be denied outright.
      this.toast.warning('無法存取剪貼簿，請手動選取複製');
    }
  }
}
