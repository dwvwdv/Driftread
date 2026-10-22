import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
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
import { ObTabs } from '../../../ui/tabs/tabs';

const ALERT_LABELS: Record<SourceActivityAlert, string> = {
  new_articles: '新增量偏高',
  revision_updates: '版本更新偏多',
  revisions_per_article: '每篇平均版本偏高',
};

/** #117: 24 hours and 7 days; the API accepts only these two windows. */
const WINDOWS = [
  { hours: 24, tab: '24 小時', meta: 'LAST 24H', label: '最近 24 小時' },
  { hours: 168, tab: '7 天', meta: 'LAST 7D', label: '最近 7 天' },
] as const;

/**
 * #114 read-only observability beside the daily storage history: recent
 * per-source ingest/revision rates and per-table heap/TOAST/index sizes with
 * MVCC counters. Display only — nothing here throttles, archives or vacuums.
 * Each panel loads and fails independently.
 */
@Component({
  selector: 'app-storage-insights',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe, ObCard, ObCallout, ObLoading, ObEmpty, ObTabs],
  templateUrl: './storage-insights.html',
  styleUrl: './storage-insights.scss',
})
export class StorageInsights implements OnInit {
  private admin = inject(AdminService);

  protected activity = signal<IngestActivity | null>(null);
  protected activityLoading = signal(true);
  protected tables = signal<TableStorageHistory | null>(null);
  protected tablesLoading = signal(true);
  protected readonly windowTabs = WINDOWS.map((w) => w.tab);
  protected windowIndex = signal(0);
  protected readonly window = computed(() => WINDOWS[this.windowIndex()]);
  /**
   * Labels follow the window the server answered for, not the tab: an older
   * API ignores `hours` and returns 24 hours, which must not read as 7 days.
   */
  protected readonly shown = computed(() => {
    const hours = this.activity()?.window_hours;
    return WINDOWS.find((w) => w.hours === hours) ?? this.window();
  });
  // Separate counters: switching the window must not drop a table response.
  private activityRequest = 0;
  private tablesRequest = 0;

  ngOnInit(): void {
    this.load();
  }

  /** Called by the dashboard's refresh button; stale responses are dropped. */
  load(): void {
    this.loadActivity();
    const request = ++this.tablesRequest;
    this.tablesLoading.set(true);
    this.admin.tableStorage().subscribe({
      next: (tables) => {
        if (request !== this.tablesRequest) return;
        this.tables.set(tables);
        this.tablesLoading.set(false);
      },
      error: () => {
        if (request !== this.tablesRequest) return;
        this.tables.set(null);
        this.tablesLoading.set(false);
      },
    });
  }

  protected onWindow(index: number): void {
    if (index === this.windowIndex()) return;
    this.windowIndex.set(index);
    this.loadActivity();
  }

  private loadActivity(): void {
    const request = ++this.activityRequest;
    this.activityLoading.set(true);
    this.admin.ingestActivity(10, this.window().hours).subscribe({
      next: (activity) => {
        if (request !== this.activityRequest) return;
        this.activity.set(activity);
        this.activityLoading.set(false);
      },
      error: () => {
        if (request !== this.activityRequest) return;
        this.activity.set(null);
        this.activityLoading.set(false);
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
