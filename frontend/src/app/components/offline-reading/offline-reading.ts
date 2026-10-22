import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../services/auth';
import { OfflineReadingService } from '../../offline/offline-reading';
import { OfflineView } from '../../offline/offline-state';
import { toggleOffline } from '../../offline/offline-toggle';
import { OfflineKind } from '../../offline/offline-store';
import { stripHtml } from '../../shared/html';
import { ObCallout } from '../../ui/callout/callout';
import { ObIcon } from '../../ui/icon/icon';
import { ObListRow } from '../../ui/list-row/list-row';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ObEmpty, ObLoading } from '../../ui/state/state';
import { ToastService } from '../../ui/toast/toast';

/**
 * /me/offline — the PWA reading exit (issue #92). Lists the bounded /me/sync
 * snapshot (most recent 100 summaries) cached on this device, readable
 * offline, with read / favorite / read-later queued for replay.
 */
@Component({
  selector: 'app-offline-reading',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, RouterLink, ObCallout, ObIcon, ObListRow, ObPageHeader, ObEmpty, ObLoading],
  templateUrl: './offline-reading.html',
  styleUrl: './offline-reading.scss',
})
export class OfflineReading {
  protected auth = inject(AuthService);
  protected offline = inject(OfflineReadingService);
  private toast = inject(ToastService);

  /** Previews derived once per snapshot, not per change-detection pass. */
  protected rows = computed(() =>
    this.offline.items().map((view) => ({ view, preview: stripHtml(view.article.summary) })),
  );

  protected async sync(): Promise<void> {
    const result = await this.offline.sync();
    if (!result.ok) {
      const message = this.offline.error();
      if (message) this.toast.danger(message);
      return;
    }
    if (result.dropped > 0) {
      this.toast.warning(`${result.dropped} 項離線操作因文章已無法存取而取消`);
    } else {
      this.toast.success('已同步到這台裝置');
    }
  }

  protected toggle(view: OfflineView, kind: OfflineKind): Promise<void> {
    return toggleOffline(this.offline, this.toast, view, kind);
  }
}
