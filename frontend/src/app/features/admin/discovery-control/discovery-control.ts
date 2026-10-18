import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  OnDestroy,
  OnInit,
  Output,
  inject,
  signal,
} from '@angular/core';
import { AdminService, DiscoveryControl } from '../../../services/admin';
import { ObCard } from '../../../ui/card/card';
import { ToastService } from '../../../ui/toast/toast';

@Component({
  selector: 'app-discovery-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ObCard],
  template: `
    <ob-card heading="自動探索" meta="EXPLORATION">
      <p role="status">
        {{
          loading()
            ? '讀取狀態中…'
            : !state()
              ? '無法讀取探索狀態'
              : !state()!.deployment_enabled
                ? '部署尚未開放自動探索'
                : state()!.enabled
                  ? '已啟用'
                  : '已暫停'
        }}
      </p>
      <p class="hint">
        暫停新的採集、探測與背景評審，保留待探索佇列。既有來源的 RSS
        更新與人工審核照常。正在跑的探索通常於一秒內取消；已送出的請求或資料庫交易仍可能完成。
      </p>
      @if (state() && !state()!.deployment_enabled) {
        <p class="hint">請先由維運開放自動探索。這裡的啟用選項不會繞過部署限制。</p>
      }
      <div cardActions>
        <button
          type="button"
          class="ob-btn ob-btn--primary ob-btn--sm"
          [disabled]="
            busy() || loading() || !state() || (!state()!.enabled && !state()!.deployment_enabled)
          "
          (click)="toggle()"
        >
          {{ busy() ? '更新中…' : state()?.enabled ? '暫停探索' : '啟用探索' }}
        </button>
        <button
          type="button"
          class="ob-btn ob-btn--ghost ob-btn--sm"
          [disabled]="busy()"
          (click)="load()"
        >
          重新讀取狀態
        </button>
      </div>
    </ob-card>
  `,
  styles: [
    `
      .hint {
        color: var(--text-dim);
        font-size: var(--text-sm);
        margin-top: var(--sp-2);
      }
      :host {
        display: block;
        margin-bottom: var(--sp-5);
      }
    `,
  ],
})
export class DiscoveryControlPanel implements OnInit, OnDestroy {
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  @Output() activeChange = new EventEmitter<boolean>();
  protected state = signal<DiscoveryControl | null>(null);
  protected busy = signal(false);
  protected loading = signal(false);
  private sequence = 0;
  ngOnInit(): void {
    this.load();
  }
  ngOnDestroy(): void {
    ++this.sequence;
  }
  protected load(): void {
    if (this.busy()) return;
    const seq = ++this.sequence;
    this.loading.set(true);
    this.activeChange.emit(false);
    this.admin.discoveryControl().subscribe({
      next: (state) => {
        if (seq === this.sequence) {
          this.state.set(state);
          this.loading.set(false);
          this.activeChange.emit(state.effective_enabled);
        }
      },
      error: () => {
        if (seq === this.sequence) {
          this.state.set(null);
          this.loading.set(false);
        }
      },
    });
  }
  protected toggle(): void {
    const current = this.state();
    if (!current || this.busy() || this.loading()) return;
    const seq = ++this.sequence;
    this.busy.set(true);
    this.admin.setDiscoveryEnabled(!current.enabled, current.version).subscribe({
      next: (state) => {
        if (seq !== this.sequence) return;
        this.state.set(state);
        this.busy.set(false);
        this.activeChange.emit(state.effective_enabled);
        this.toast.success(
          state.enabled ? '已啟用探索，將依排程接續佇列' : '已暫停探索，待探索佇列已保留',
        );
      },
      error: () => {
        if (seq === this.sequence) {
          this.busy.set(false);
          this.load();
        }
      },
    });
  }
}
