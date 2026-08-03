import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * Busy indicator.
 *
 * A rotating square rather than the usual arc: a circular spinner is the one
 * element that would reintroduce a radius into a zero-radius system, and it reads
 * as borrowed from somewhere else the moment it appears next to the offset-shadow
 * cards.
 *
 * Under prefers-reduced-motion the rotation is replaced by an opacity pulse — the
 * "still working" signal is preserved without the spin.
 */
@Component({
  selector: 'ob-spinner',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span
      class="box"
      [style.width.px]="size"
      [style.height.px]="size"
      role="status"
      [attr.aria-label]="label"
    ></span>
  `,
  styles: [
    `
      :host {
        display: inline-flex;
      }

      .box {
        border: var(--border-width) solid var(--border);
        border-top-color: var(--accent);
        border-right-color: var(--accent);
        animation: ob-spin 720ms linear infinite;
      }

      @keyframes ob-spin {
        to {
          transform: rotate(360deg);
        }
      }

      @keyframes ob-pulse {
        50% {
          opacity: 0.25;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .box {
          animation: ob-pulse 1.4s ease-in-out infinite;
          border-color: var(--accent);
        }
      }
    `,
  ],
})
export class ObSpinner {
  @Input() size = 24;
  @Input() label = '載入中';
}
