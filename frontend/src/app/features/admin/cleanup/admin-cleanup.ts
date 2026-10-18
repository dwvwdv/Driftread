import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { AdminService, CleanupAction, CleanupItem, CleanupKind } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObPaginator } from '../../../ui/paginator/paginator';
import { ObEmpty, ObLoading } from '../../../ui/state/state';
import { ObListRow } from '../../../ui/list-row/list-row';

@Component({
  selector: 'app-admin-cleanup',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObPageHeader, ObPaginator, ObEmpty, ObLoading, ObListRow],
  templateUrl: './admin-cleanup.html',
  styleUrl: './admin-cleanup.scss',
})
export class AdminCleanup implements OnInit, OnDestroy {
  private admin = inject(AdminService);
  private confirm = inject(ConfirmService);
  private toast = inject(ToastService);
  protected kind: CleanupKind = 'feeds';
  protected rule = 'category';
  protected state = 'active';
  protected query = '';
  protected items = signal<CleanupItem[]>([]);
  protected total = signal(0);
  protected page = signal(1);
  protected selected = signal(new Set<string>());
  protected loading = signal(false);
  protected busy = signal(false);
  private sequence = 0;
  private destroyed = false;

  ngOnInit(): void {
    this.load();
  }
  ngOnDestroy(): void {
    this.destroyed = true;
    this.sequence++;
  }
  protected changeKind(): void {
    this.rule = this.kind === 'feeds' ? 'category' : 'url';
    this.reset();
  }
  protected reset(): void {
    this.page.set(1);
    this.selected.set(new Set());
    this.load();
  }
  protected go(page: number): void {
    if (!this.busy()) {
      this.page.set(page);
      this.selected.set(new Set());
      this.load();
    }
  }
  protected load(keep = new Set<string>()): void {
    const seq = ++this.sequence;
    this.loading.set(true);
    this.admin
      .listCleanup(this.kind, this.rule, this.state, this.query.trim(), (this.page() - 1) * 50)
      .subscribe({
        next: (data) => {
          if (seq !== this.sequence || this.destroyed) return;
          if (!data.items.length && data.total > 0 && this.page() > Math.ceil(data.total / 50)) {
            this.page.set(Math.ceil(data.total / 50));
            this.load(keep);
            return;
          }
          this.items.set(data.items);
          this.total.set(data.total);
          this.selected.set(new Set(data.items.filter((r) => keep.has(r.id)).map((r) => r.id)));
          this.loading.set(false);
        },
        error: () => {
          if (seq === this.sequence && !this.destroyed) {
            this.items.set([]);
            this.selected.set(new Set());
            this.total.set(0);
            this.loading.set(false);
          }
        },
      });
  }
  protected toggle(id: string): void {
    if (this.busy()) return;
    this.selected.update((old) => {
      const next = new Set(old);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  protected all(): void {
    if (!this.busy())
      this.selected.set(
        this.selected().size === this.items().length
          ? new Set()
          : new Set(this.items().map((r) => r.id)),
      );
  }
  protected async apply(action: CleanupAction): Promise<void> {
    if (this.busy() || this.loading() || !this.selected().size) return;
    const ids = [...this.selected()];
    const kind = this.kind;
    this.busy.set(true);
    ++this.sequence;
    try {
      const preview = await firstValueFrom(this.admin.cleanupBatch(kind, ids, action));
      if (this.destroyed) return;
      const eligible = preview.items.filter((r) => r.ok).map((r) => r.id);
      if (!eligible.length) {
        this.toast.warning('項目已不存在或尚未暫存，請重新整理；永久刪除前需先暫存。');
        return;
      }
      const sums = preview.items
        .filter((r) => r.ok)
        .reduce(
          (s, r) => ({
            articles: s.articles + (r.impact?.articles || 0),
            bookmarks: s.bookmarks + (r.impact?.bookmarks || 0),
            reads: s.reads + (r.impact?.reads || 0),
            subscriptions: s.subscriptions + (r.impact?.subscriptions || 0),
          }),
          { articles: 0, bookmarks: 0, reads: 0, subscriptions: 0 },
        );
      const label = { stage: '暫存', restore: '還原', delete: '永久刪除' }[action];
      const ok = await this.confirm.ask({
        heading: `${label} ${eligible.length} 個${kind === 'feeds' ? '來源' : '文章'}？`,
        body:
          `涉及 ${sums.articles} 篇文章、${sums.subscriptions} 個訂閱、${sums.bookmarks} 個收藏／稍後讀、${sums.reads} 筆已讀。` +
          (action === 'delete'
            ? '永久刪除會一併移除相關資料，無法還原。'
            : kind === 'feeds'
              ? '來源暫存會封存並暫停抓取；既有文章仍可閱讀。還原後恢復排程。'
              : '文章暫存會從閱讀與搜尋中隱藏，保留原 ID 與使用者資料，可隨時還原。') +
          (eligible.length < ids.length ? `另有 ${ids.length - eligible.length} 項無法處理。` : ''),
        confirmLabel: label,
        danger: action === 'delete',
      });
      if (!ok || this.destroyed) return;
      const result = await firstValueFrom(this.admin.cleanupBatch(kind, eligible, action, false));
      if (this.destroyed) return;
      const failed = new Set(
        [...preview.items.filter((r) => !r.ok), ...result.items.filter((r) => !r.ok)].map(
          (r) => r.id,
        ),
      );
      this.toast.success(
        `已${label} ${result.items.filter((r) => r.ok).length} 項，失敗 ${failed.size} 項`,
      );
      this.load(failed);
    } catch {
      /* AdminService reports safe errors; keep the selection for retry. */
    } finally {
      if (!this.destroyed) this.busy.set(false);
    }
  }
}
