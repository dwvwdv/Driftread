import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

export type StatTone = 'default' | 'accent' | 'success' | 'warning' | 'danger';

/**
 * Metric tile for the admin dashboard: a big 900-weight number under a monospace
 * label.
 *
 * `tone` tints the number only, so a grid of tiles stays scannable and a single
 * red figure actually stands out. Used for the ten DiscoveryStats counters.
 */
@Component({
  selector: 'ob-stat',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="ob-label">{{ label }}</span>
    <span class="num">{{ value }}</span>
    @if (hint) {
      <span class="hint">{{ hint }}</span>
    }
  `,
  styles: [
    `
      :host {
        display: flex;
        flex-direction: column;
        gap: var(--sp-1);
        padding: var(--sp-3) var(--sp-4);
        background: var(--surface-raised);
        border: var(--border-width) solid var(--border);
        border-top: var(--accent-bar) solid var(--border);
      }

      :host([tone='accent']) {
        border-top-color: var(--accent);
      }
      :host([tone='success']) {
        border-top-color: var(--success);
      }
      :host([tone='warning']) {
        border-top-color: var(--warning);
      }
      :host([tone='danger']) {
        border-top-color: var(--danger);
      }

      .num {
        font-size: var(--text-2xl);
        font-weight: 900;
        line-height: 1;
        color: var(--text-strong);
        font-variant-numeric: tabular-nums;
      }

      :host([tone='accent']) .num {
        color: var(--accent);
      }
      :host([tone='success']) .num {
        color: var(--success-ink);
      }
      :host([tone='warning']) .num {
        color: var(--warning-ink);
      }
      :host([tone='danger']) .num {
        color: var(--danger-ink);
      }

      .hint {
        font-size: var(--text-xs);
        color: var(--text-dim);
      }
    `,
  ],
  host: {
    '[attr.tone]': 'tone',
  },
})
export class ObStat {
  @Input({ required: true }) label!: string;
  @Input({ required: true }) value!: number | string;
  @Input() hint = '';
  @Input() tone: StatTone = 'default';
}
