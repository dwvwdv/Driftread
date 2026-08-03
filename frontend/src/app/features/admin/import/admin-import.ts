import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminService } from '../../../services/admin';
import { ObCallout } from '../../../ui/callout/callout';
import { ObCard } from '../../../ui/card/card';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ToastService } from '../../../ui/toast/toast';

/**
 * Manual entry points into the catalogue: a JSON batch, or a single URL.
 *
 * Import-by-URL is new to the console — POST /admin/feeds/from-url existed as part
 * of the open API but the UI only ever offered the JSON path, so adding one feed
 * meant hand-writing a JSON array for it.
 */
@Component({
  selector: 'app-admin-import',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCallout, ObCard, ObPageHeader],
  templateUrl: './admin-import.html',
  styleUrl: './admin-import.scss',
})
export class AdminImport {
  private admin = inject(AdminService);
  private toast = inject(ToastService);

  protected json = '';
  protected importing = signal(false);
  protected jsonError = signal('');

  protected feedUrl = '';
  protected importingUrl = signal(false);

  protected importJson(): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(this.json);
    } catch {
      // Caught before the request so a typo does not read as a server failure.
      this.jsonError.set('JSON 格式錯誤，請確認括號與引號是否完整');
      return;
    }

    this.jsonError.set('');
    // The endpoint wants {feeds: [...]}, but a bare array is the natural thing to
    // paste, so accept both.
    const body = Array.isArray(parsed) ? { feeds: parsed } : parsed;

    this.importing.set(true);
    this.admin.importFeeds(body).subscribe({
      next: (feeds) => {
        this.importing.set(false);
        this.json = '';
        this.toast.success(`成功匯入 ${feeds.length} 個信息源`);
      },
      error: () => this.importing.set(false),
    });
  }

  protected importUrl(): void {
    const url = this.feedUrl.trim();
    if (!url) {
      this.toast.warning('請輸入 feed 網址');
      return;
    }

    this.importingUrl.set(true);
    this.admin.addFeedFromUrl(url).subscribe({
      next: (feed) => {
        this.importingUrl.set(false);
        this.feedUrl = '';
        this.toast.success(`已匯入：${feed.title}`);
      },
      error: () => this.importingUrl.set(false),
    });
  }
}
