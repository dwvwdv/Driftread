import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { AdminDashboard } from './admin-dashboard';
import { AdminService } from '../../../services/admin';
import { ToastService } from '../../../ui/toast/toast';
import { ArticleStorageStats, OperationsStatus, StorageHistory } from '../../../models';

const storage: ArticleStorageStats = {
  article_count: 100,
  full_content_count: 60,
  summary_only_count: 40,
  pending_discovery_count: 10,
  compacted_count: 20,
  table_bytes: 1048576,
  index_bytes: 1048576,
  total_bytes: 2097152,
  database_bytes: 3145728,
};
const operations: OperationsStatus = {
  observed_at: '2026-10-08T00:00:00Z',
  stale_after_seconds: 90,
  retention_days: 30,
  workers: [],
  recent_runs: [],
  recent_failures: 0,
};

function dashboard(overrides: Record<string, unknown> = {}) {
  TestBed.configureTestingModule({
    imports: [AdminDashboard],
    providers: [
      provideRouter([]),
      {
        provide: AdminService,
        useValue: {
          stats: () => of({ candidates_pending: 12 }),
          discoveryControl: () =>
            of({ enabled: true, deployment_enabled: true, effective_enabled: true, version: 1 }),
          listUnhealthy: () => of([]),
          operations: () => of(operations),
          storageStats: () => of(storage),
          storageHistory: () =>
            of({
              observed_at: operations.observed_at,
              retention_days: 90,
              points: [],
              growth: null,
            }),
          ingestActivity: () => throwError(() => new Error('not under test')),
          tableStorage: () => throwError(() => new Error('not under test')),
          globalSettings: () => throwError(() => new Error('not under test')),
          ...overrides,
        },
      },
      { provide: ToastService, useValue: {} },
    ],
  });
  const fixture = TestBed.createComponent(AdminDashboard);
  fixture.detectChanges();
  return fixture;
}

describe('AdminDashboard operational snapshots', () => {
  it('shows TTL recent results and the rolling deletion delay', () => {
    const fixture = dashboard({
      operations: () =>
        of({
          ...operations,
          latest_runs: {
            article_ttl: {
              id: 'ttl-run',
              worker_id: 'w1',
              kind: 'article_ttl',
              status: 'succeeded',
              started_at: operations.observed_at,
              finished_at: operations.observed_at,
              summary: { deleted: 21 },
              error: null,
            },
          },
          article_ttl_24h: { deleted: 42, average_delay_seconds: 5400 },
        }),
    });
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector('[data-ttl-daily]')?.textContent).toContain(
      '最近 24 小時到期排程刪除 42 篇',
    );
    expect(element.querySelector('[data-ttl-daily]')?.textContent).toContain(
      '平均在到期後 1.5 小時刪除',
    );
    expect(element.querySelector('[aria-label="各排程最近一次執行"]')?.textContent).toContain(
      'article_ttl',
    );
    expect(element.querySelector('app-article-ttl-control')).not.toBe(null);
  });

  it('shows known empty history and distinguishes insufficient samples', () => {
    const fixture = dashboard();
    expect(fixture.nativeElement.textContent).toContain('尚無每日容量紀錄');
    const component = fixture.componentInstance as unknown as {
      history: { set: (h: StorageHistory) => void };
    };
    component.history.set({
      observed_at: operations.observed_at,
      retention_days: 90,
      points: [{ ...storage, snapshot_date: '2026-10-08', observed_at: operations.observed_at }],
      growth: null,
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('至少需要兩筆跨 24 小時');
    expect(fixture.nativeElement.querySelector('table').textContent).toContain('100');
  });

  it('shows signed growth and its actual observation period', () => {
    const fixture = dashboard({
      storageHistory: () =>
        of({
          observed_at: operations.observed_at,
          retention_days: 90,
          points: [
            { ...storage, snapshot_date: '2026-10-08', observed_at: operations.observed_at },
          ],
          growth: {
            start_at: '2026-10-04T00:00:00Z',
            end_at: operations.observed_at,
            elapsed_days: 4,
            article_count_per_day: 25,
            table_bytes_per_day: -1048576,
            index_bytes_per_day: 0,
            total_bytes_per_day: -1048576,
            database_bytes_per_day: 2097152,
          },
        }),
    });
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('跨 4.0 天');
    expect(text).toContain('-1.0 MiB');
    expect(text).toContain('+2.0 MiB');
  });

  it('keeps current storage usable if history cannot be loaded', () => {
    const fixture = dashboard({
      storageHistory: () => throwError(() => new Error('missing migration')),
    });
    expect(fixture.nativeElement.textContent).toContain('容量歷史未知');
    expect(fixture.nativeElement.textContent).toContain('2.0 MiB');
  });

  it('ignores stale history success and error after a reload', () => {
    const responses: Subject<StorageHistory>[] = [];
    const fixture = dashboard({
      storageHistory: () => {
        const response = new Subject<StorageHistory>();
        responses.push(response);
        return response;
      },
    });
    (fixture.componentInstance as unknown as { load: () => void }).load();
    responses[1].next({
      observed_at: operations.observed_at,
      retention_days: 90,
      points: [{ ...storage, snapshot_date: '2026-10-08', observed_at: operations.observed_at }],
      growth: null,
    });
    responses[0].error(new Error('old request'));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('至少需要兩筆');
    expect(fixture.nativeElement.textContent).not.toContain('容量歷史未知');
  });
  it('shows durable queue backlog and distinguishes unresolved watchdog incidents', () => {
    const fixture = dashboard({
      operations: () =>
        of({
          ...operations,
          queue: {
            queued: 3,
            running: 1,
            dead: 2,
            succeeded: 5,
            next_available_at: operations.observed_at,
          },
          alerts: [
            {
              id: 'a1',
              worker_id: 'worker-1',
              kind: 'worker_stale',
              created_at: operations.observed_at,
              resolved_at: null,
            },
            {
              id: 'a2',
              worker_id: 'worker-2',
              kind: 'worker_stale',
              created_at: operations.observed_at,
              resolved_at: operations.observed_at,
            },
          ],
        }),
    });
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('待執行工作');
    expect(text).toContain('重試耗盡');
    expect(text).toContain('下一筆可執行時間');
    expect(text).toContain('排程器失聯');
    expect(text).toContain('已恢復');
  });
  it('labels missing heartbeats unknown and shows storage without claiming growth', () => {
    const fixture = dashboard();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('狀態未知：尚無排程器心跳');
    expect(text).toContain('2.0 MiB');
    expect(text).toContain('單次快照無法推算成長率');
  });

  it('keeps discovery and storage usable when operations fails', () => {
    const fixture = dashboard({ operations: () => throwError(() => new Error('unavailable')) });
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('排程器狀態未知');
    expect(text).toContain('待審核候選');
    expect(text).toContain('2.0 MiB');
    expect(text).toContain('手動觸發');
  });

  it('shows stale worker and failed run even when storage fails', () => {
    const fixture = dashboard({
      storageStats: () => throwError(() => new Error('unavailable')),
      operations: () =>
        of({
          ...operations,
          workers: [
            {
              worker_id: 'w1',
              hostname: 'worker',
              heartbeat_at: operations.observed_at,
              status: 'running',
              age_seconds: 120,
              stale: true,
            },
          ],
          recent_runs: [
            {
              id: 'r1',
              worker_id: 'w1',
              kind: 'discovery',
              status: 'interrupted',
              started_at: operations.observed_at,
              finished_at: null,
              summary: {},
              error: null,
            },
          ],
          recent_failures: 1,
        }),
    });
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('心跳過期');
    expect(text).toContain('中斷');
    expect(text).toContain('儲存量未知');
  });

  it('ignores an older snapshot when a manual reload overtakes it', () => {
    const responses: Subject<OperationsStatus>[] = [];
    const fixture = dashboard({
      operations: () => {
        const response = new Subject<OperationsStatus>();
        responses.push(response);
        return response;
      },
    });
    const component = fixture.componentInstance as unknown as { load: () => void };
    component.load();
    responses[1].next({ ...operations, recent_failures: 2 });
    responses[0].next({ ...operations, recent_failures: 9 });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('2 輪失敗');
    expect(fixture.nativeElement.textContent).not.toContain('9 輪失敗');
  });

  it('shows the worker-reported retention state and latest retention run', () => {
    const run = {
      id: 'ret',
      worker_id: 'w1',
      kind: 'retention',
      status: 'failed',
      started_at: operations.observed_at,
      finished_at: operations.observed_at,
      summary: {},
      error: 'RuntimeError',
    };
    const fixture = dashboard({
      operations: () =>
        of({
          ...operations,
          latest_runs: { retention: run, storage: null },
          worker_config: {
            worker_id: 'w1',
            hostname: 'worker-host',
            reported_at: operations.observed_at,
            stale: false,
            status: 'running',
            config: {
              refresh_enabled: true,
              discovery_deployment_enabled: true,
              retention: {
                enabled: true,
                mode: 'apply',
                days: 30,
                batch_size: 200,
                interval_minutes: 1440,
              },
            },
          },
        }),
    });
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector('[data-retention="apply"]')?.textContent).toContain(
      '每 1440 分鐘直接縮減最多 200 篇超過 30 天',
    );
    const latest = element.querySelector('[aria-label="各排程最近一次執行"]')!.textContent!;
    expect(latest).toContain('retention');
    expect(latest).toContain('失敗');
    expect(latest).toContain('RuntimeError');
    expect(latest).toContain('30 天內沒有執行紀錄');
  });

  it('distinguishes a disabled retention scheduler from an unreported one', () => {
    const disabled = dashboard({
      operations: () =>
        of({
          ...operations,
          worker_config: {
            worker_id: 'w1',
            hostname: 'worker-host',
            reported_at: operations.observed_at,
            stale: true,
            status: 'running',
            config: {
              refresh_enabled: true,
              discovery_deployment_enabled: false,
              retention: {
                enabled: false,
                mode: 'disabled',
                days: 30,
                batch_size: 200,
                interval_minutes: 1440,
              },
            },
          },
        }),
    });
    const element: HTMLElement = disabled.nativeElement;
    expect(element.querySelector('[data-retention="disabled"]')).not.toBeNull();
    expect(element.textContent).toContain('心跳已過期');
    TestBed.resetTestingModule();
    const unknown = dashboard();
    expect(unknown.nativeElement.querySelector('[data-retention="unknown"]')).not.toBeNull();
    expect(unknown.nativeElement.querySelector('[data-retention="disabled"]')).toBeNull();
  });

  it('reloads the storage insights panel with the dashboard refresh', () => {
    let calls = 0;
    const fixture = dashboard({
      ingestActivity: () => {
        calls++;
        return throwError(() => new Error('unavailable'));
      },
    });
    expect(calls).toBe(1);
    expect(fixture.nativeElement.textContent).toContain('來源入庫量未知');
    expect(fixture.nativeElement.textContent).toContain('2.0 MiB');
    (fixture.componentInstance as unknown as { load: () => void }).load();
    expect(calls).toBe(2);
  });
});
