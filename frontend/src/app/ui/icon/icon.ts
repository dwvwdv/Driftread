import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

/**
 * The full icon set. A literal union rather than `string` so strictTemplates
 * rejects a typo at build time instead of rendering an empty box.
 */
export type IconName =
  | 'search'
  | 'close'
  | 'check'
  | 'plus'
  | 'menu'
  | 'chevron-left'
  | 'chevron-right'
  | 'page-first'
  | 'page-last'
  | 'arrow-left'
  | 'heart'
  | 'star'
  | 'bookmark'
  | 'refresh'
  | 'archive'
  | 'restore'
  | 'external'
  | 'download'
  | 'upload'
  | 'sun'
  | 'moon'
  | 'monitor'
  | 'logout'
  | 'lock'
  | 'block'
  | 'alert'
  | 'info';

/**
 * Inline SVG icon.
 *
 * Replaces MatIcon and, with it, the Material Icons webfont that index.html used
 * to pull from the Google Fonts CDN — one less external dependency and one less
 * render-blocking request.
 *
 * Every icon is a literal <path>/<circle>/<line> selected by @switch. Nothing goes
 * through [innerHTML] and nothing is passed to bypassSecurityTrust*, so the
 * component presents no sanitiser surface at all.
 *
 * Shapes are drawn rather than borrowed: 24x24, stroke-only, 2px, with *square*
 * caps and joins, which is what keeps them consistent with a zero-radius,
 * hard-edged visual language. `filled` switches the three toggleable icons
 * (heart/star/bookmark) to a solid state.
 *
 * Decorative by default (aria-hidden). Pass [label] when the icon is the only
 * content of a control and carries the meaning itself.
 */
@Component({
  selector: 'ob-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg
      [attr.width]="size"
      [attr.height]="size"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="square"
      stroke-linejoin="miter"
      [attr.aria-hidden]="label ? null : 'true'"
      [attr.role]="label ? 'img' : null"
      [attr.aria-label]="label || null"
      focusable="false"
    >
      @switch (name) {
        @case ('search') {
          <circle cx="10.5" cy="10.5" r="6.5" />
          <line x1="15.5" y1="15.5" x2="21" y2="21" />
        }
        @case ('close') {
          <line x1="5" y1="5" x2="19" y2="19" />
          <line x1="19" y1="5" x2="5" y2="19" />
        }
        @case ('check') {
          <polyline points="4,12.5 9.5,18 20,6" />
        }
        @case ('plus') {
          <line x1="12" y1="4" x2="12" y2="20" />
          <line x1="4" y1="12" x2="20" y2="12" />
        }
        @case ('menu') {
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        }
        @case ('chevron-left') {
          <polyline points="15,4 7,12 15,20" />
        }
        @case ('chevron-right') {
          <polyline points="9,4 17,12 9,20" />
        }
        @case ('page-first') {
          <polyline points="18,4 10,12 18,20" />
          <line x1="6" y1="4" x2="6" y2="20" />
        }
        @case ('page-last') {
          <polyline points="6,4 14,12 6,20" />
          <line x1="18" y1="4" x2="18" y2="20" />
        }
        @case ('arrow-left') {
          <line x1="20" y1="12" x2="4" y2="12" />
          <polyline points="10,6 4,12 10,18" />
        }
        @case ('heart') {
          <path
            d="M12 20.5 4.6 13.1a4.7 4.7 0 1 1 7.4-5.6 4.7 4.7 0 1 1 7.4 5.6z"
            [attr.fill]="filled ? 'currentColor' : 'none'"
          />
        }
        @case ('star') {
          <polygon
            points="12,3 14.9,9.6 22,10.3 16.6,15 18.2,22 12,18.3 5.8,22 7.4,15 2,10.3 9.1,9.6"
            [attr.fill]="filled ? 'currentColor' : 'none'"
          />
        }
        @case ('bookmark') {
          <path d="M6 3h12v18l-6-4.8L6 21z" [attr.fill]="filled ? 'currentColor' : 'none'" />
        }
        @case ('refresh') {
          <polyline points="21,5 21,11 15,11" />
          <path d="M20 11a8 8 0 1 0-1.6 5" />
        }
        @case ('archive') {
          <rect x="3" y="4" width="18" height="4" />
          <path d="M5 8v12h14V8" />
          <line x1="10" y1="13" x2="14" y2="13" />
        }
        @case ('restore') {
          <path d="M5 20V8h14v12" />
          <rect x="3" y="4" width="18" height="4" />
          <polyline points="9,15 12,12 15,15" />
        }
        @case ('external') {
          <path d="M14 4h6v6" />
          <line x1="20" y1="4" x2="11" y2="13" />
          <path d="M18 14v6H4V6h6" />
        }
        @case ('download') {
          <line x1="12" y1="3" x2="12" y2="15" />
          <polyline points="7,10 12,15 17,10" />
          <path d="M4 18v3h16v-3" />
        }
        @case ('upload') {
          <line x1="12" y1="16" x2="12" y2="4" />
          <polyline points="7,9 12,4 17,9" />
          <path d="M4 18v3h16v-3" />
        }
        @case ('sun') {
          <circle cx="12" cy="12" r="4.5" />
          <line x1="12" y1="1.5" x2="12" y2="4" />
          <line x1="12" y1="20" x2="12" y2="22.5" />
          <line x1="1.5" y1="12" x2="4" y2="12" />
          <line x1="20" y1="12" x2="22.5" y2="12" />
          <line x1="4.6" y1="4.6" x2="6.4" y2="6.4" />
          <line x1="17.6" y1="17.6" x2="19.4" y2="19.4" />
          <line x1="19.4" y1="4.6" x2="17.6" y2="6.4" />
          <line x1="6.4" y1="17.6" x2="4.6" y2="19.4" />
        }
        @case ('moon') {
          <path d="M20 14.5A9 9 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z" />
        }
        @case ('monitor') {
          <rect x="3" y="4" width="18" height="13" />
          <line x1="8" y1="21" x2="16" y2="21" />
          <line x1="12" y1="17" x2="12" y2="21" />
        }
        @case ('logout') {
          <path d="M14 4H4v16h10" />
          <line x1="20" y1="12" x2="9" y2="12" />
          <polyline points="16,7 21,12 16,17" />
        }
        @case ('lock') {
          <rect x="4" y="10" width="16" height="11" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        }
        @case ('block') {
          <circle cx="12" cy="12" r="9" />
          <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
        }
        @case ('alert') {
          <path d="M12 3 22 21H2z" />
          <line x1="12" y1="10" x2="12" y2="15" />
          <line x1="12" y1="17.8" x2="12" y2="18" />
        }
        @case ('info') {
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="11" x2="12" y2="17" />
          <line x1="12" y1="7" x2="12" y2="7.2" />
        }
      }
    </svg>
  `,
  styles: [
    `
      :host {
        display: inline-flex;
        flex: none;
        align-items: center;
        justify-content: center;
      }
    `,
  ],
})
export class ObIcon {
  @Input({ required: true }) name!: IconName;
  @Input() size = 18;
  @Input() filled = false;
  /** Set when the icon carries meaning on its own; otherwise it stays aria-hidden. */
  @Input() label = '';
}
