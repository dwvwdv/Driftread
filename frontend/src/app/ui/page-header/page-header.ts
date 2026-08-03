import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * Page title block: heading, optional monospace kicker above it, and a right-hand
 * action slot.
 *
 * Every page uses this. That consistency is most of what makes the chrome read as
 * one system rather than nine separately-styled screens.
 */
@Component({
  selector: 'ob-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="text">
      @if (kicker) {
        <p class="ob-label">{{ kicker }}</p>
      }
      <h1>{{ heading }}</h1>
      @if (subtitle) {
        <p class="sub">{{ subtitle }}</p>
      }
    </div>
    <div class="actions">
      <ng-content />
    </div>
  `,
  styles: [
    `
      :host {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: var(--sp-4);
        flex-wrap: wrap;
        margin-bottom: var(--sp-6);
        padding-bottom: var(--sp-4);
        border-bottom: var(--border-width) solid var(--border);
      }

      .text {
        display: flex;
        flex-direction: column;
        gap: var(--sp-1);
        min-width: 0;
      }

      h1 {
        font-size: var(--text-xl);
      }

      .sub {
        font-size: var(--text-sm);
        color: var(--text-dim);
      }

      .actions:not(:empty) {
        display: flex;
        align-items: center;
        gap: var(--sp-2);
        flex-wrap: wrap;
      }
    `,
  ],
})
export class ObPageHeader {
  @Input({ required: true }) heading!: string;
  @Input() kicker = '';
  @Input() subtitle = '';
}
