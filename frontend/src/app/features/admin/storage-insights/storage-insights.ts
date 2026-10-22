import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { AdminService } from '../../../services/admin';
import { formatMiB, formatSignedMiB } from '../../../shared/bytes';
import {
  IngestActivity,
  SourceActivityAlert,
  TableDelta,
  TableStorageHistory,
} from '../../../models';
import { ObCard } from '../../../ui/card/card';
import { ObCallout } from '../../../ui/callout/callout';
import { ObLoading, ObEmpty } from '../../../ui/state/state';

const ALERT_LABELS: Record<SourceActivityAlert, string> = {
  new_articles: '新增量偏高',
  revision_updates: '版本更新偏多',
  revisions_per_article: '每篇平均版本偏高',
};

/**
 * #114 read-only observability beside the daily storage history: recent
 * per-source ingest/revision rates and per-table heap/TOAST/index sizes with
 * MVCC counters. Display only — nothing here throttles, archives or vacuums.
 * Each panel loads and fails independently.
 */
@Component({
  selector: 'app-storage-insights',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe, ObCard, ObCallout, ObLoading, ObEmpty],
  templateUrl: './storage-insights.html',
  styleUrl: './storage-insights.scss',
})
export class StorageInsights implements OnInit {
  private admin = inject(AdminService);

  protected activity = signal<IngestActivity | null>(null);
  protected activityLoading = signal(true);
  protected tables = signal<TableStorageHistory | null>(null);
  protected tablesLoading = signal(true);
  private request = 0;

  ngOnInit(): void {
    this.load();
  }

  /** Called by the dashboard's refresh button; stale responses are dropped. */
  load(): void {
    const request = ++this.request;
    this.activityLoading.set(true);
    this.tablesLoading.set(true);
    this.admin.ingestActivity().subscribe({
      next: (activity) => {
        if (request !== this.request) return;
        this.activity.set(activity);
        this.activityLoading.set(false);
      },
      error: () => {
        if (request !== this.request) return;
        this.activity.set(null);
        this.activityLoading.set(false);
      },
    });
    this.admin.tableStorage().subscribe({
      next: (tables) => {
        if (request !== this.request) return;
        this.tables.set(tables);
        this.tablesLoading.set(false);
      },
      error: () => {
        if (request !== this.request) return;
        this.tables.set(null);
        this.tablesLoading.set(false);
      },
    });
  }

  protected alertLabels(alerts: SourceActivityAlert[]): string {
    return alerts.map((alert) => ALERT_LABELS[alert] ?? alert).join('、');
  }

  protected bytes(value: number | null): string {
    return formatMiB(value);
  }

  protected percent(ratio: number): string {
    return `${(ratio * 100).toFixed(1)}%`;
  }

  protected signedBytes(value: number | null): string {
    return formatSignedMiB(value);
  }

  /** Counters differenced across a stats reset are unknown, never negative. */
  protected count(delta: TableDelta, value: number | null): string {
    if (!delta.comparable || value === null) return '無法計算';
    return value.toLocaleString('en-US');
  }
}
