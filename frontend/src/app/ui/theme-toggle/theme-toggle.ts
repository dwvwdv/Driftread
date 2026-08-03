import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { ObIcon, IconName } from '../icon/icon';
import { ThemeMode, ThemeService } from '../../services/theme';

interface Option {
  mode: ThemeMode;
  icon: IconName;
  label: string;
}

/**
 * Three-way theme control: follow the system, or pin light or dark.
 *
 * A three-state control rather than a two-state switch because "follow the system"
 * is a real preference, not the absence of one — a binary toggle silently
 * overrides the OS the first time it is touched and never gives that back.
 *
 * Exposed as a radiogroup: one tab stop, arrow keys between options, which is what
 * a set of mutually exclusive choices should be.
 */
@Component({
  selector: 'ob-theme-toggle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObIcon],
  template: `
    <div class="group" role="radiogroup" aria-label="佈景主題">
      @for (option of options; track option.mode) {
        <button
          type="button"
          role="radio"
          class="opt"
          [class.opt--active]="theme.mode() === option.mode"
          [attr.aria-checked]="theme.mode() === option.mode"
          [attr.tabindex]="theme.mode() === option.mode ? 0 : -1"
          [title]="option.label"
          (click)="theme.set(option.mode)"
          (keydown)="onKeydown($event)"
        >
          <ob-icon [name]="option.icon" [size]="15" [label]="option.label" />
        </button>
      }
    </div>
  `,
  styles: [
    `
      .group {
        display: inline-flex;
        border: var(--border-width) solid var(--border);
      }

      .opt {
        display: grid;
        place-items: center;
        width: 30px;
        height: 26px;
        background: transparent;
        border: none;
        color: var(--text-dim);
        cursor: pointer;
        transition: color var(--dur) var(--ease);
      }

      .opt + .opt {
        border-left: var(--border-width-thin) solid var(--border);
      }

      .opt:hover {
        color: var(--text);
      }

      .opt--active {
        background: var(--surface-sunken);
        color: var(--accent);
      }

      .opt:focus-visible {
        outline: var(--focus-ring-width) solid var(--accent);
        outline-offset: calc(-1 * var(--focus-ring-width));
      }

      @media (pointer: coarse) {
        .opt {
          width: var(--tap-target);
          height: 36px;
        }
      }
    `,
  ],
})
export class ObThemeToggle {
  protected theme = inject(ThemeService);

  protected options: readonly Option[] = [
    { mode: 'system', icon: 'monitor', label: '跟隨系統' },
    { mode: 'light', icon: 'sun', label: '淺色' },
    { mode: 'dark', icon: 'moon', label: '深色' },
  ];

  protected onKeydown(event: KeyboardEvent): void {
    const current = this.options.findIndex((o) => o.mode === this.theme.mode());
    const last = this.options.length - 1;
    let next: number;

    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = current === last ? 0 : current + 1;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = current <= 0 ? last : current - 1;
        break;
      default:
        return;
    }

    event.preventDefault();
    const option = this.options[next];
    if (!option) return;
    this.theme.set(option.mode);

    // Roving tabindex: focus must follow the selection or the next Tab leaves the
    // group from a control that is now tabindex="-1".
    const buttons = (event.currentTarget as HTMLElement)
      .closest('.group')
      ?.querySelectorAll<HTMLButtonElement>('.opt');
    buttons?.[next]?.focus();
  }
}
