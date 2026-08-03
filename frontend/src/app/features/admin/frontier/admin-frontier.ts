import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminService } from '../../../services/admin';
import { DiscoverySource, DiscoveryTarget } from '../../../models';
import { ObCard } from '../../../ui/card/card';
import { ObIcon } from '../../../ui/icon/icon';
import { ObListRow } from '../../../ui/list-row/list-row';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ObPaginator } from '../../../ui/paginator/paginator';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { clampPage } from '../../../shared/paging';

const TARGET_STATUSES = ['pending', 'done', 'blocked', 'exhausted', 'rejected'] as const;

/**
 * The crawl frontier: seed URLs, the probe queue, and the directory sources that
 * feed it.
 *
 * The queue listing and host blocking are both new to the UI. GET
 * /admin/discovery/targets and PATCH /targets/{id}/block existed, but the console
 * could only ever add to the frontier — there was no way to see what was in it or
 * to stop a misbehaving host.
 */
@Component({
  selector: 'app-admin-frontier',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCard, ObIcon, ObListRow, ObLoading, ObEmpty, ObPageHeader, ObPaginator],
  templateUrl: './admin-frontier.html',
  styleUrl: './admin-frontier.scss',
})
export class AdminFrontier implements OnInit {
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  private confirm = inject(ConfirmService);

  protected readonly statuses = TARGET_STATUSES;

  // ── Seeding ───────────────────────────────────────────────────────────────
  protected seedUrls = '';
  protected seeding = signal(false);

  // ── Probe queue ───────────────────────────────────────────────────────────
  protected targets = signal<DiscoveryTarget[]>([]);
  protected targetsTotal = signal(0);
  protected status = signal<string>('pending');
  protected page = signal(1);
  protected pageSize = signal(20);
  protected loadingTargets = signal(true);

  // ── Directory sources ─────────────────────────────────────────────────────
  protected sources = signal<DiscoverySource[]>([]);
  protected loadingSources = signal(true);
  protected newSourceUrl = '';
  protected newSourceKind: 'links_page' | 'opml' = 'links_page';

  ngOnInit(): void {
    this.loadTargets();
    this.loadSources();
  }

  // ── Seeding ───────────────────────────────────────────────────────────────

  protected seed(): void {
    const urls = this.seedUrls
      .split('\n')
      .map((u) => u.trim())
      .filter(Boolean);

    if (!urls.length) {
      this.toast.warning('請至少輸入一個網址');
      return;
    }

    this.seeding.set(true);
    this.admin.seedTargets(urls).subscribe({
      next: (result) => {
        this.seeding.set(false);
        this.seedUrls = '';
        const parts = [
          `新增 ${result.accepted}`,
          `重排 ${result.requeued}`,
          `略過 ${result.skipped}`,
        ];
        if (result.rejected.length) parts.push(`拒絕 ${result.rejected.length}`);
        this.toast.success(parts.join('、'));
        this.loadTargets();
      },
      error: () => this.seeding.set(false),
    });
  }

  // ── Probe queue ───────────────────────────────────────────────────────────

  protected loadTargets(): void {
    this.loadingTargets.set(true);
    this.admin.listTargets(this.status(), this.page(), this.pageSize()).subscribe({
      next: (result) => {
        this.targets.set(result.items);
        this.targetsTotal.set(result.total);
        this.loadingTargets.set(false);

        // Self-correcting, because the new total is only known once the response
        // lands. Blocking a host can drop several targets at once, so the page
        // just requested may already be past the end — and the backend answers an
        // out-of-range page with an empty list rather than an error, which the
        // template renders as "nothing here" while hiding the paginator.
        //
        // Terminates: clampPage only ever returns a *lower* page, so at most one
        // correction happens per load.
        const clamped = clampPage(this.page(), result.total, this.pageSize());
        if (result.items.length === 0 && result.total > 0 && clamped !== this.page()) {
          this.page.set(clamped);
          this.loadTargets();
        }
      },
      error: () => this.loadingTargets.set(false),
    });
  }

  protected onStatus(event: Event): void {
    this.status.set((event.target as HTMLSelectElement).value);
    this.page.set(1);
    this.loadTargets();
  }

  protected onPage(page: number): void {
    this.page.set(page);
    this.loadTargets();
  }

  protected onPageSize(size: number): void {
    this.pageSize.set(size);
    this.page.set(1);
    this.loadTargets();
  }

  protected async block(target: DiscoveryTarget): Promise<void> {
    const ok = await this.confirm.ask({
      heading: `封鎖 ${target.host}？`,
      body: '封鎖的是整個網域，不只是這一個網址。之後這個網域上的任何來源都不會再被提議，而且 API 沒有解除封鎖的端點。',
      confirmLabel: '封鎖網域',
      danger: true,
    });
    if (!ok) return;

    this.admin.blockTarget(target.id).subscribe({
      next: () => {
        this.toast.success(`已封鎖 ${target.host}`);
        this.loadTargets();
      },
      error: () => undefined,
    });
  }

  // ── Directory sources ─────────────────────────────────────────────────────

  protected loadSources(): void {
    this.loadingSources.set(true);
    this.admin.listSources().subscribe({
      next: (sources) => {
        this.sources.set(sources);
        this.loadingSources.set(false);
      },
      error: () => this.loadingSources.set(false),
    });
  }

  protected addSource(): void {
    const url = this.newSourceUrl.trim();
    if (!url) {
      this.toast.warning('請輸入目錄網址');
      return;
    }

    this.admin.addSources([{ url, kind: this.newSourceKind }]).subscribe({
      next: () => {
        this.toast.success('已新增目錄來源');
        this.newSourceUrl = '';
        this.loadSources();
      },
      error: () => undefined,
    });
  }

  protected toggleSource(source: DiscoverySource): void {
    this.admin.updateSource(source.id, { enabled: !source.enabled }).subscribe({
      next: () => this.loadSources(),
      error: () => undefined,
    });
  }

  protected reloadDefaults(): void {
    this.admin.reloadDefaultSources().subscribe({
      next: (result) => {
        this.toast.success(`已載入 ${result.loaded} 個預設來源`);
        this.loadSources();
      },
      error: () => undefined,
    });
  }
}
