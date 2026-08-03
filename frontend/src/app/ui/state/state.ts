import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { ObIcon } from '../icon/icon';
import { ObSpinner } from '../spinner/spinner';

/**
 * The three page states.
 *
 * These replace four verbatim copies of the same `.center` / `.error { color: red }`
 * / `.empty` rules that were duplicated across feed-list, feed-detail,
 * article-reader and recommendations. Beyond removing the duplication, having one
 * implementation is what lets the error state actually announce itself: the old
 * `<p class="error">` was invisible to assistive technology.
 */

@Component({
  selector: 'ob-loading',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObSpinner],
  template: `
    <div class="wrap">
      <ob-spinner [size]="28" [label]="label" />
      <p class="ob-label">{{ label }}</p>
    </div>
  `,
  styles: [
    `
      .wrap {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: var(--sp-3);
        padding: var(--sp-12) var(--sp-4);
      }
    `,
  ],
})
export class ObLoading {
  @Input() label = '載入中';
}

@Component({
  selector: 'ob-error',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObIcon],
  // role="alert" so the message reaches assistive technology the moment it
  // appears, which the plain <p class="error"> it replaces never did.
  template: `
    <div class="wrap" role="alert">
      <ob-icon name="alert" [size]="20" />
      <p class="msg">{{ message }}</p>
      <ng-content />
    </div>
  `,
  styles: [
    `
      .wrap {
        display: flex;
        align-items: flex-start;
        gap: var(--sp-3);
        padding: var(--sp-4);
        background: var(--surface-raised);
        border: var(--border-width) solid var(--danger);
        border-left-width: var(--accent-bar);
        color: var(--danger-ink);
      }

      .msg {
        flex: 1;
        font-size: var(--text-sm);
      }
    `,
  ],
})
export class ObError {
  @Input({ required: true }) message!: string;
}

@Component({
  selector: 'ob-empty',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap">
      <p class="msg">{{ message }}</p>
      <!-- Optional recovery action, e.g. 「再推薦一批」 or a link back to the catalogue. -->
      <ng-content />
    </div>
  `,
  styles: [
    `
      .wrap {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: var(--sp-4);
        padding: var(--sp-12) var(--sp-4);
        text-align: center;
        color: var(--text-dim);
      }

      .msg {
        font-size: var(--text-sm);
      }
    `,
  ],
})
export class ObEmpty {
  @Input({ required: true }) message!: string;
}
