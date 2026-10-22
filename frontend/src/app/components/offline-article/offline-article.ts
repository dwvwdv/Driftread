import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { OfflineReadingService } from '../../offline/offline-reading';
import { OfflineKind } from '../../offline/offline-store';
import { ObIcon } from '../../ui/icon/icon';
import { ObEmpty, ObLoading } from '../../ui/state/state';
import { ToastService } from '../../ui/toast/toast';
import { toggleOffline } from '../../offline/offline-toggle';

/**
 * /me/offline/:id — one cached article. The /me/sync snapshot carries the
 * summary only (never the body), so this renders that summary through
 * [innerHTML] (Angular's sanitizer; never bypassSecurityTrustHtml) and links
 * to the full online reader and the original page.
 */
@Component({
  selector: 'app-offline-article',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, RouterLink, ObIcon, ObEmpty, ObLoading],
  templateUrl: './offline-article.html',
  styleUrl: './offline-article.scss',
})
export class OfflineArticle {
  protected offline = inject(OfflineReadingService);
  private toast = inject(ToastService);

  private readonly id = toSignal(
    inject(ActivatedRoute).paramMap.pipe(map((params) => params.get('id') ?? '')),
    { initialValue: '' },
  );

  protected view = computed(
    () => this.offline.items().find((v) => v.article.id === this.id()) ?? null,
  );

  protected toggle(kind: OfflineKind): Promise<void> {
    const view = this.view();
    return view ? toggleOffline(this.offline, this.toast, view, kind) : Promise.resolve();
  }
}
