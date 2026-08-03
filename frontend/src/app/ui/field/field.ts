import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * Label + control + hint/error wrapper.
 *
 * The label element *wraps* the projected control rather than pointing at it with
 * `for`. That is deliberate: an implicit association needs no generated id, cannot
 * fall out of sync, and works for whatever the caller projects — input, textarea
 * or select — without this component knowing anything about it.
 *
 * The error line carries role="alert" so a validation message is announced when it
 * appears.
 *
 * Usage:
 *   <ob-field label="搜尋信息源" hint="標題或描述">
 *     <input class="ob-input" [(ngModel)]="query" />
 *   </ob-field>
 */
@Component({
  selector: 'ob-field',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <label class="wrap">
      <span class="ob-label">{{ label }}</span>
      <ng-content />
    </label>
    @if (error) {
      <p class="err" role="alert">{{ error }}</p>
    } @else if (hint) {
      <p class="hint">{{ hint }}</p>
    }
  `,
  styles: [
    `
      :host {
        display: flex;
        flex-direction: column;
        gap: var(--sp-1);
        min-width: 0;
      }

      .wrap {
        display: flex;
        flex-direction: column;
        gap: var(--sp-1);
        cursor: pointer;
      }

      .hint,
      .err {
        font-size: var(--text-xs);
      }

      .hint {
        color: var(--text-dim);
      }

      .err {
        color: var(--danger-ink);
        font-weight: 700;
      }
    `,
  ],
})
export class ObField {
  @Input({ required: true }) label!: string;
  @Input() hint = '';
  @Input() error = '';
}
