import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { ObIcon } from '../icon/icon';

/**
 * Pagination control.
 *
 * Pages are 1-based here, unlike MatPaginator's 0-based `pageIndex`. The API is
 * 1-based (`?page=1`), and having the component match it removes the ±1 that every
 * call site would otherwise have to remember.
 *
 * Collapses to prev / indicator / next below 640px — first and last are a
 * convenience, not a requirement, and they are the first things worth dropping
 * when there is no room.
 */
@Component({
  selector: 'ob-paginator',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObIcon],
  template: `
    <nav class="bar" aria-label="分頁導覽">
      <span class="count">共 {{ total }} 筆</span>

      <div class="controls">
        <button
          type="button"
          class="ob-btn ob-btn--ghost ob-btn--sm ob-btn--icon edge"
          aria-label="第一頁"
          [disabled]="page <= 1"
          (click)="go(1)"
        >
          <ob-icon name="page-first" [size]="14" />
        </button>
        <button
          type="button"
          class="ob-btn ob-btn--ghost ob-btn--sm ob-btn--icon"
          aria-label="上一頁"
          [disabled]="page <= 1"
          (click)="go(page - 1)"
        >
          <ob-icon name="chevron-left" [size]="14" />
        </button>

        <span class="indicator" aria-current="page">{{ page }} / {{ totalPages }}</span>

        <button
          type="button"
          class="ob-btn ob-btn--ghost ob-btn--sm ob-btn--icon"
          aria-label="下一頁"
          [disabled]="page >= totalPages"
          (click)="go(page + 1)"
        >
          <ob-icon name="chevron-right" [size]="14" />
        </button>
        <button
          type="button"
          class="ob-btn ob-btn--ghost ob-btn--sm ob-btn--icon edge"
          aria-label="最後一頁"
          [disabled]="page >= totalPages"
          (click)="go(totalPages)"
        >
          <ob-icon name="page-last" [size]="14" />
        </button>
      </div>

      <label class="size">
        <span class="ob-visually-hidden">每頁顯示筆數</span>
        <select
          class="ob-select"
          [value]="pageSize"
          (change)="changeSize($event)"
          aria-label="每頁顯示筆數"
        >
          @for (option of pageSizeOptions; track option) {
            <option [value]="option">每頁 {{ option }}</option>
          }
        </select>
      </label>
    </nav>
  `,
  styles: [
    `
      .bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: var(--sp-4);
        flex-wrap: wrap;
        padding-top: var(--sp-5);
        margin-top: var(--sp-5);
        border-top: var(--border-width) solid var(--border);
      }

      .count {
        font-family: var(--font-mono);
        font-size: var(--text-xs);
        color: var(--text-dim);
      }

      .controls {
        display: flex;
        align-items: center;
        gap: var(--sp-2);
      }

      .indicator {
        min-width: 72px;
        text-align: center;
        font-family: var(--font-mono);
        font-size: var(--text-sm);
        font-weight: 700;
        color: var(--text-strong);
        font-variant-numeric: tabular-nums;
      }

      .size .ob-select {
        width: auto;
        font-size: var(--text-xs);
        font-family: var(--font-mono);
        padding-block: var(--sp-1);
      }

      @media (max-width: 640px) {
        .bar {
          justify-content: center;
        }

        .edge {
          display: none;
        }
      }
    `,
  ],
})
export class ObPaginator {
  @Input({ required: true }) total = 0;
  @Input() page = 1;
  @Input() pageSize = 20;
  /** Every option must stay at or under the backend's page_size cap of 100. */
  @Input() pageSizeOptions: readonly number[] = [10, 20, 50];

  @Output() pageChange = new EventEmitter<number>();
  @Output() pageSizeChange = new EventEmitter<number>();

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.total / this.pageSize));
  }

  go(page: number): void {
    const clamped = Math.min(Math.max(1, page), this.totalPages);
    if (clamped === this.page) return;
    this.page = clamped;
    this.pageChange.emit(clamped);
  }

  changeSize(event: Event): void {
    const size = Number((event.target as HTMLSelectElement).value);
    if (!size || size === this.pageSize) return;
    this.pageSize = size;
    // Row 60 of page 3 does not exist at a larger page size, so reset rather than
    // leaving the caller on an out-of-range page.
    this.page = 1;
    this.pageSizeChange.emit(size);
  }
}
