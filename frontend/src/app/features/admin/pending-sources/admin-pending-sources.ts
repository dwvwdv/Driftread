import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { AdminService } from '../../../services/admin';
import { DiscoveryTarget } from '../../../models';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObPaginator } from '../../../ui/paginator/paginator';
import { ObEmpty, ObLoading } from '../../../ui/state/state';
import { ObListRow } from '../../../ui/list-row/list-row';
import { DiscoveryControlPanel } from '../discovery-control/discovery-control';

@Component({
  selector: 'app-admin-pending-sources',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DatePipe,
    ObPageHeader,
    ObPaginator,
    ObEmpty,
    ObLoading,
    ObListRow,
    DiscoveryControlPanel,
  ],
  templateUrl: './admin-pending-sources.html',
  styleUrl: '../cleanup/admin-cleanup.scss',
})
export class AdminPendingSources implements OnInit, OnDestroy {
  private admin = inject(AdminService);
  private confirm = inject(ConfirmService);
  private toast = inject(ToastService);
  protected items = signal<DiscoveryTarget[]>([]);
  protected total = signal(0);
  protected page = signal(1);
  protected selected = signal(new Set<string>());
  protected busy = signal(false);
  protected loading = signal(false);
  private sequence = 0;
  private destroyed = false;
  ngOnInit(): void {
    this.load();
  }
  ngOnDestroy(): void {
    this.destroyed = true;
    ++this.sequence;
  }
  protected load(keep = new Set<string>()): void {
    if (this.busy()) return;
    const seq = ++this.sequence;
    this.loading.set(true);
    this.admin.pendingTargets((this.page() - 1) * 50).subscribe({
      next: (data) => {
        if (seq !== this.sequence || this.destroyed) return;
        if (!data.items.length && data.total && this.page() > Math.ceil(data.total / 50)) {
          this.page.set(Math.ceil(data.total / 50));
          this.load(keep);
          return;
        }
        this.items.set(data.items);
        this.total.set(data.total);
        this.loading.set(false);
        this.selected.set(new Set(data.items.filter((r) => keep.has(r.id)).map((r) => r.id)));
      },
      error: () => {
        if (seq === this.sequence && !this.destroyed) {
          this.items.set([]);
          this.total.set(0);
          this.selected.set(new Set());
          this.loading.set(false);
        }
      },
    });
  }
  protected go(page: number): void {
    if (!this.busy()) {
      this.page.set(page);
      this.selected.set(new Set());
      this.load();
    }
  }
  protected toggle(id: string): void {
    if (!this.busy())
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
  protected async remove(ids = [...this.selected()]): Promise<void> {
    if (this.busy() || this.loading() || !ids.length) return;
    this.busy.set(true);
    ++this.sequence;
    try {
      const ok = await this.confirm.ask({
        heading: `移除 ${ids.length} 個待探索來源？`,
        body: '這些網址將停止排隊，保留取消標記以避免重新加入。已開始的請求可能完成，但不再新增候選；不影響已收錄來源，也不封鎖整個網域。',
        confirmLabel: '移除待探索來源',
        danger: true,
      });
      if (!ok || this.destroyed) return;
      const result = await firstValueFrom(this.admin.removeTargets(ids));
      if (this.destroyed) return;
      const failed = new Set(result.items.filter((r) => !r.ok).map((r) => r.id));
      this.toast.success(
        `已移除 ${result.items.filter((r) => r.ok).length} 項，失敗 ${failed.size} 項`,
      );
      this.busy.set(false);
      this.load(failed);
    } catch {
      /* Selection remains available for retry. */
    } finally {
      if (!this.destroyed) this.busy.set(false);
    }
  }
}
