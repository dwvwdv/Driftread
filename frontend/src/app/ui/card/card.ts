import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

export type CardTone = 'default' | 'accent' | 'danger' | 'special';

/**
 * The standard Offbeat panel: a bordered surface with an optional title bar on a
 * deeper ground, sitting on a hard offset shadow.
 *
 * `tone` recolours the shadow only — the border and fill stay constant so a page
 * of cards still reads as one grid rather than a colour chart.
 *
 * Content projection slots:
 *   [cardActions] — a footer row, separated by a rule
 *   default       — the body
 *
 * Note the shadow is drawn outside the border box: whatever lays these out needs
 * trailing room (`.ob-grid` already includes it).
 */
@Component({
  selector: 'ob-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (heading) {
      <div class="head">
        <span class="ob-label title">{{ heading }}</span>
        @if (meta) {
          <span class="meta">{{ meta }}</span>
        }
      </div>
    }
    <div class="body" [class.body--flush]="flush">
      <ng-content />
    </div>
    <div class="actions">
      <ng-content select="[cardActions]" />
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
        background: var(--surface-raised);
        border: var(--border-width) solid var(--border);
        box-shadow: var(--offset) var(--offset) 0 0 var(--shadow-offset);
      }

      :host([tone='accent']) {
        box-shadow: var(--offset) var(--offset) 0 0 var(--accent);
      }
      :host([tone='danger']) {
        box-shadow: var(--offset) var(--offset) 0 0 var(--danger);
      }
      :host([tone='special']) {
        box-shadow: var(--offset) var(--offset) 0 0 var(--special);
      }

      .head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--sp-3);
        padding: var(--sp-2) var(--sp-4);
        background: var(--surface-sunken);
        border-bottom: var(--border-width) solid var(--border);
      }

      .title {
        color: var(--accent);
      }

      .meta {
        font-family: var(--font-mono);
        font-size: var(--text-xs);
        color: var(--text-dim);
      }

      .body {
        padding: var(--sp-4);
      }

      .body--flush {
        padding: 0;
      }

      /* Collapses to nothing when no [cardActions] content is projected, so a card
         without a footer gets no stray padding or rule. */
      .actions:not(:empty) {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sp-2);
        padding: var(--sp-3) var(--sp-4);
        border-top: var(--border-width-thin) solid var(--border);
      }
    `,
  ],
  host: {
    '[attr.tone]': 'tone',
  },
})
export class ObCard {
  @Input() heading = '';
  @Input() meta = '';
  @Input() tone: CardTone = 'default';
  /** Drop the body padding — for cards whose content is a full-bleed list. */
  @Input() flush = false;
}
