import { LiveAnnouncer } from '@angular/cdk/a11y';
import { Overlay, OverlayRef } from '@angular/cdk/overlay';
import { ComponentPortal } from '@angular/cdk/portal';
import { ChangeDetectionStrategy, Component, Injectable, inject, signal } from '@angular/core';
import { ObIcon, IconName } from '../icon/icon';

export type ToastTone = 'info' | 'success' | 'warning' | 'danger';

export interface Toast {
  id: number;
  text: string;
  tone: ToastTone;
}

/** How long each tone stays up. Failures need longer to read than confirmations. */
const DURATION: Record<ToastTone, number> = {
  info: 3500,
  success: 3500,
  warning: 5000,
  danger: 6000,
};

const MAX_VISIBLE = 3;
let nextId = 0;

/**
 * Transient feedback, replacing MatSnackBar.
 *
 * Built on CDK Overlay rather than a component sitting in a layout template,
 * because the main caller is AdminService — a service, with no view of its own.
 * A template-hosted toast outlet would force every service call to route its
 * result back through a component just to report it.
 *
 * Visual toasts do not reach assistive technology on their own, so every message
 * also goes to LiveAnnouncer: assertive for failures, polite otherwise.
 */
@Injectable({ providedIn: 'root' })
export class ToastService {
  private overlay = inject(Overlay);
  private announcer = inject(LiveAnnouncer);
  private ref: OverlayRef | null = null;
  private host: ObToastHost | null = null;

  info(text: string): void {
    this.show(text, 'info');
  }
  success(text: string): void {
    this.show(text, 'success');
  }
  warning(text: string): void {
    this.show(text, 'warning');
  }
  danger(text: string): void {
    this.show(text, 'danger');
  }

  show(text: string, tone: ToastTone = 'info', durationMs = DURATION[tone]): void {
    this.ensureHost().push({ id: nextId++, text, tone }, durationMs);
    this.announcer.announce(text, tone === 'danger' ? 'assertive' : 'polite');
  }

  private ensureHost(): ObToastHost {
    if (this.host) return this.host;

    this.ref = this.overlay.create({
      positionStrategy: this.overlay.position().global().bottom('24px').centerHorizontally(),
      // Toasts never block the page underneath.
      hasBackdrop: false,
      scrollStrategy: this.overlay.scrollStrategies.noop(),
      panelClass: 'ob-toast-panel',
    });
    this.host = this.ref.attach(new ComponentPortal(ObToastHost)).instance;
    return this.host;
  }
}

@Component({
  selector: 'ob-toast-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObIcon],
  template: `
    <div class="stack">
      @for (toast of toasts(); track toast.id) {
        <div
          class="toast"
          [attr.tone]="toast.tone"
          (mouseenter)="pause(toast.id)"
          (mouseleave)="resume(toast.id)"
          (focusin)="pause(toast.id)"
          (focusout)="resume(toast.id)"
        >
          <ob-icon [name]="iconFor(toast.tone)" [size]="16" />
          <span class="text">{{ toast.text }}</span>
          <button type="button" class="close" aria-label="關閉通知" (click)="dismiss(toast.id)">
            <ob-icon name="close" [size]="14" />
          </button>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .stack {
        display: flex;
        flex-direction: column;
        gap: var(--sp-2);
        align-items: center;
        /* The overlay pane is only as wide as this, so leave the rest of the
           screen click-through. */
        pointer-events: none;
      }

      .toast {
        pointer-events: auto;
        display: flex;
        align-items: center;
        gap: var(--sp-3);
        max-width: min(92vw, 480px);
        padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-4);
        background: var(--surface-raised);
        color: var(--text);
        border: var(--border-width) solid var(--border);
        border-left: var(--accent-bar) solid var(--info);
        box-shadow: var(--offset) var(--offset) 0 0 var(--shadow-offset);
        font-size: var(--text-sm);
      }

      .toast[tone='success'] {
        border-left-color: var(--success);
      }
      .toast[tone='warning'] {
        border-left-color: var(--warning);
      }
      .toast[tone='danger'] {
        border-left-color: var(--danger);
        box-shadow: var(--offset) var(--offset) 0 0 var(--danger);
      }

      .text {
        flex: 1;
        overflow-wrap: anywhere;
      }

      .close {
        flex: none;
        display: grid;
        place-items: center;
        width: 24px;
        height: 24px;
        background: transparent;
        border: none;
        color: var(--text-dim);
        cursor: pointer;
      }

      .close:hover {
        color: var(--text-strong);
      }

      .close:focus-visible {
        outline: var(--focus-ring-width) solid var(--accent);
        outline-offset: 1px;
      }
    `,
  ],
})
export class ObToastHost {
  toasts = signal<Toast[]>([]);

  private timers = new Map<number, ReturnType<typeof setTimeout>>();
  private durations = new Map<number, number>();

  push(toast: Toast, durationMs: number): void {
    this.toasts.update((list) => [...list, toast].slice(-MAX_VISIBLE));
    this.durations.set(toast.id, durationMs);
    this.arm(toast.id, durationMs);
  }

  dismiss(id: number): void {
    this.clear(id);
    this.durations.delete(id);
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }

  /** Hovering or focusing holds the message open — a failure you are still
      reading should not vanish mid-sentence. */
  pause(id: number): void {
    this.clear(id);
  }

  resume(id: number): void {
    const duration = this.durations.get(id);
    if (duration !== undefined) this.arm(id, duration);
  }

  iconFor(tone: ToastTone): IconName {
    switch (tone) {
      case 'success':
        return 'check';
      case 'warning':
      case 'danger':
        return 'alert';
      default:
        return 'info';
    }
  }

  private arm(id: number, durationMs: number): void {
    this.clear(id);
    this.timers.set(
      id,
      setTimeout(() => this.dismiss(id), durationMs),
    );
  }

  private clear(id: number): void {
    const timer = this.timers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.timers.delete(id);
    }
  }
}
