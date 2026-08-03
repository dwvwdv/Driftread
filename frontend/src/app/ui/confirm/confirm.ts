import { A11yModule } from '@angular/cdk/a11y';
import { Overlay } from '@angular/cdk/overlay';
import { ComponentPortal } from '@angular/cdk/portal';
import { ChangeDetectionStrategy, Component, Injectable, inject, signal } from '@angular/core';
import { Subject, filter, firstValueFrom, map, merge, take } from 'rxjs';

export interface ConfirmOptions {
  heading: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Paints the confirm button as destructive. */
  danger?: boolean;
}

/**
 * Modal confirmation for irreversible actions.
 *
 * New — Material never provided a dialog here, so destructive admin operations
 * previously fired on a single click. The one that most needs it is blocking a
 * discovery target: that blocks the entire host, not just the row the operator is
 * looking at, and the API exposes no way to undo it.
 *
 * Returns a promise, so callers read as `if (await confirm.ask({...}))`.
 */
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  private overlay = inject(Overlay);

  async ask(options: ConfirmOptions): Promise<boolean> {
    const ref = this.overlay.create({
      positionStrategy: this.overlay.position().global().centerHorizontally().centerVertically(),
      hasBackdrop: true,
      backdropClass: 'ob-confirm-backdrop',
      scrollStrategy: this.overlay.scrollStrategies.block(),
    });

    const dialog = ref.attach(new ComponentPortal(ObConfirmDialog)).instance;
    dialog.options.set(options);

    // Whatever opened the dialog must get focus back, or a keyboard user lands at
    // the top of the document when it closes.
    const opener = document.activeElement as HTMLElement | null;

    // Escape and backdrop both mean "no", same as the cancel button.
    const dismissed = merge(
      ref.backdropClick(),
      ref.keydownEvents().pipe(filter((e) => e.key === 'Escape')),
    ).pipe(map(() => false));

    try {
      return await firstValueFrom(merge(dialog.answered, dismissed).pipe(take(1)));
    } finally {
      ref.dispose();
      opener?.focus();
    }
  }
}

@Component({
  selector: 'ob-confirm-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [A11yModule],
  template: `
    @if (options(); as opts) {
      <div
        class="panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="ob-confirm-heading"
        aria-describedby="ob-confirm-body"
        cdkTrapFocus
        [cdkTrapFocusAutoCapture]="true"
      >
        <div class="head">
          <span class="ob-label">{{ opts.danger ? '危險操作' : '請確認' }}</span>
        </div>
        <div class="body">
          <h2 id="ob-confirm-heading">{{ opts.heading }}</h2>
          <p id="ob-confirm-body">{{ opts.body }}</p>
        </div>
        <div class="actions">
          <button type="button" class="ob-btn ob-btn--ghost" (click)="answer(false)">
            {{ opts.cancelLabel || '取消' }}
          </button>
          <button
            type="button"
            [class]="opts.danger ? 'ob-btn ob-btn--danger' : 'ob-btn ob-btn--primary'"
            (click)="answer(true)"
          >
            {{ opts.confirmLabel || '確認' }}
          </button>
        </div>
      </div>
    }
  `,
  styles: [
    `
      .panel {
        width: min(92vw, 440px);
        background: var(--surface-raised);
        border: var(--border-width) solid var(--border);
        box-shadow: var(--offset) var(--offset) 0 0 var(--shadow-offset);
      }

      .head {
        padding: var(--sp-2) var(--sp-4);
        background: var(--surface-sunken);
        border-bottom: var(--border-width) solid var(--border);
      }

      .body {
        display: flex;
        flex-direction: column;
        gap: var(--sp-2);
        padding: var(--sp-5) var(--sp-4);
      }

      h2 {
        font-size: var(--text-lg);
      }

      p {
        font-size: var(--text-sm);
        color: var(--text-dim);
      }

      .actions {
        display: flex;
        justify-content: flex-end;
        gap: var(--sp-2);
        padding: var(--sp-3) var(--sp-4);
        border-top: var(--border-width-thin) solid var(--border);
      }
    `,
  ],
})
export class ObConfirmDialog {
  options = signal<ConfirmOptions | null>(null);
  answered = new Subject<boolean>();

  answer(result: boolean): void {
    this.answered.next(result);
  }
}
