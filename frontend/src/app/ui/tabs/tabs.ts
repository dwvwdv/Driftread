import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  Output,
  inject,
} from '@angular/core';

let nextId = 0;

/**
 * Tab strip plus the panel its content sits in.
 *
 * Material gave WAI-ARIA tabs for free; this buys them back explicitly. Against
 * the Tabs pattern that means: role tablist/tab/tabpanel, aria-selected,
 * aria-controls and aria-labelledby wired both ways, a roving tabindex so the
 * group is a single tab stop, and Left/Right/Home/End to move between tabs.
 *
 * Activation is automatic (moving focus selects) — correct for tabs whose panels
 * are already loaded, which is the case for both uses here.
 *
 * The caller owns panel content and switches it:
 *
 *   <ob-tabs [tabs]="['收藏', '稍後閱讀']" [(selected)]="index">
 *     @if (index === 0) { ... } @else { ... }
 *   </ob-tabs>
 */
@Component({
  selector: 'ob-tabs',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="strip" role="tablist" (keydown)="onKeydown($event)">
      @for (tab of tabs; track tab; let i = $index) {
        <button
          type="button"
          role="tab"
          class="tab"
          [id]="tabId(i)"
          [class.tab--active]="i === selected"
          [attr.aria-selected]="i === selected"
          [attr.aria-controls]="panelId"
          [attr.tabindex]="i === selected ? 0 : -1"
          (click)="select(i)"
        >
          {{ tab }}
        </button>
      }
    </div>
    <div
      class="panel"
      role="tabpanel"
      [id]="panelId"
      [attr.aria-labelledby]="tabId(selected)"
      tabindex="0"
    >
      <ng-content />
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .strip {
        display: flex;
        gap: 0;
        border-bottom: var(--border-width) solid var(--border);
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }

      .tab {
        flex: none;
        background: transparent;
        border: none;
        /* Reserves the active underline so selecting a tab shifts nothing. */
        border-bottom: var(--accent-bar) solid transparent;
        margin-bottom: calc(-1 * var(--border-width));
        padding: var(--sp-3) var(--sp-4);
        font-family: var(--font-mono);
        font-size: var(--text-xs);
        font-weight: 700;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        color: var(--text-dim);
        cursor: pointer;
        white-space: nowrap;
        transition: color var(--dur) var(--ease);
      }

      .tab:hover {
        color: var(--text);
      }

      .tab:focus-visible {
        outline: var(--focus-ring-width) solid var(--accent);
        outline-offset: calc(-1 * var(--focus-ring-width));
      }

      .tab--active {
        color: var(--accent);
        border-bottom-color: var(--accent);
      }

      .panel {
        padding-top: var(--sp-5);
      }

      /* The panel is focusable so keyboard users can reach its content directly,
         but it is not itself an interactive control — no ring on click. */
      .panel:focus:not(:focus-visible) {
        outline: none;
      }
    `,
  ],
})
export class ObTabs {
  private host = inject(ElementRef<HTMLElement>);
  private uid = nextId++;

  @Input({ required: true }) tabs: readonly string[] = [];
  @Input() selected = 0;
  @Output() selectedChange = new EventEmitter<number>();

  get panelId(): string {
    return `ob-tabpanel-${this.uid}`;
  }

  tabId(index: number): string {
    return `ob-tab-${this.uid}-${index}`;
  }

  select(index: number): void {
    if (index === this.selected) return;
    this.selected = index;
    this.selectedChange.emit(index);
  }

  onKeydown(event: KeyboardEvent): void {
    const last = this.tabs.length - 1;
    let next: number | null = null;

    switch (event.key) {
      case 'ArrowRight':
        next = this.selected === last ? 0 : this.selected + 1;
        break;
      case 'ArrowLeft':
        next = this.selected === 0 ? last : this.selected - 1;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = last;
        break;
      default:
        return;
    }

    event.preventDefault();
    this.select(next);
    // Focus has to follow selection, otherwise the roving tabindex leaves focus
    // on a tab that is now -1 and the next Tab press escapes the group.
    const el = this.host.nativeElement as HTMLElement;
    el.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }
}
