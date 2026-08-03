import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * A dense row for lists that want density rather than a card grid — article
 * listings, bookmarks, admin queues.
 *
 * Replaces mat-list-item and its matListItemTitle / matListItemLine /
 * matListItemMeta slots. Projection slots here:
 *
 *   [rowTitle] — the headline
 *   [rowLines] — secondary lines (URL, counts, timestamps)
 *   [rowMeta]  — right-hand actions
 *
 * The row is a plain container, not a link. Where the whole row should navigate,
 * the caller puts an <a> in [rowTitle] — one tab stop, real link semantics, and no
 * clickable <div>. (feed-list previously put [routerLink] straight on a
 * <mat-card>, which produced neither.)
 */
@Component({
  selector: 'ob-list-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="main">
      <div class="title">
        <ng-content select="[rowTitle]" />
      </div>
      <div class="lines">
        <ng-content select="[rowLines]" />
      </div>
      <ng-content />
    </div>
    <div class="meta">
      <ng-content select="[rowMeta]" />
    </div>
  `,
  styles: [
    `
      :host {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: var(--sp-4);
        padding: var(--sp-3) var(--sp-4);
        border-bottom: var(--border-width-thin) solid var(--border);
      }

      :host(:last-of-type) {
        border-bottom: none;
      }

      :host(:hover) {
        background: var(--surface-sunken);
      }

      .main {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: var(--sp-1);
      }

      .title {
        font-size: var(--text-base);
        font-weight: 700;
        color: var(--text-strong);
        /* Feed and article titles are third-party text and run long. */
        overflow-wrap: anywhere;
      }

      .lines:not(:empty) {
        display: flex;
        flex-direction: column;
        gap: 2px;
        font-size: var(--text-xs);
        color: var(--text-dim);
        overflow-wrap: anywhere;
      }

      .meta:not(:empty) {
        display: flex;
        align-items: center;
        gap: var(--sp-2);
        flex: none;
        flex-wrap: wrap;
        justify-content: flex-end;
      }

      /* Actions drop below the text rather than squeezing it on narrow screens. */
      @media (max-width: 640px) {
        :host {
          flex-direction: column;
          align-items: stretch;
          gap: var(--sp-3);
        }

        .meta:not(:empty) {
          justify-content: flex-start;
        }
      }
    `,
  ],
})
export class ObListRow {}
