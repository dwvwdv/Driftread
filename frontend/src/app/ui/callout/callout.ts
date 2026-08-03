import { ChangeDetectionStrategy, Component, Input } from '@angular/core';
import { ObIcon, IconName } from '../icon/icon';

export type CalloutTone = 'info' | 'success' | 'warning' | 'danger';

/**
 * Inline notice with a 4px Aurora bar down the left edge.
 *
 * For conditions that persist on the page — an unconfigured backend, a partially
 * failed import, a disabled feature flag. Transient feedback goes through
 * ToastService instead.
 *
 * Only `danger` gets role="alert"; the quieter tones would be interrupting for no
 * reason.
 */
@Component({
  selector: 'ob-callout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObIcon],
  template: `
    <ob-icon [name]="icon" [size]="18" />
    <div class="body">
      @if (heading) {
        <p class="head">{{ heading }}</p>
      }
      <ng-content />
    </div>
  `,
  styles: [
    `
      :host {
        display: flex;
        align-items: flex-start;
        gap: var(--sp-3);
        padding: var(--sp-3) var(--sp-4);
        background: var(--surface-raised);
        border: var(--border-width-thin) solid var(--border);
        border-left: var(--accent-bar) solid var(--info);
        font-size: var(--text-sm);
      }

      :host([tone='info']) {
        border-left-color: var(--info);
        color: var(--info-ink);
      }
      :host([tone='success']) {
        border-left-color: var(--success);
        color: var(--success-ink);
      }
      :host([tone='warning']) {
        border-left-color: var(--warning);
        color: var(--caution-ink);
      }
      :host([tone='danger']) {
        border-left-color: var(--danger);
        color: var(--danger-ink);
      }

      .body {
        flex: 1;
        min-width: 0;
        color: var(--text);
      }

      .head {
        font-weight: 700;
        color: inherit;
        margin-bottom: var(--sp-1);
      }
    `,
  ],
  host: {
    '[attr.tone]': 'tone',
    '[attr.role]': 'tone === "danger" ? "alert" : null',
  },
})
export class ObCallout {
  @Input() tone: CalloutTone = 'info';
  @Input() heading = '';

  get icon(): IconName {
    switch (this.tone) {
      case 'success':
        return 'check';
      case 'warning':
      case 'danger':
        return 'alert';
      default:
        return 'info';
    }
  }
}
