import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';
import { AdminDashboard } from './admin-dashboard';
import { AdminService } from '../../../services/admin';
import { ToastService } from '../../../ui/toast/toast';
import { ArticleStorageStats, OperationsStatus } from '../../../models';

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
          listUnhealthy: () => of([]),
          operations: () => of(operations),
          storageStats: () => of(storage),
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
  it('shows durable queue backlog and distinguishes unresolved watchdog incidents', () => {
    const fixture = dashboard({ operations: () => of({
      ...operations,
      queue: { queued: 3, running: 1, dead: 2, succeeded: 5, next_available_at: operations.observed_at },
      alerts: [
        { id: 'a1', worker_id: 'worker-1', kind: 'worker_stale', created_at: operations.observed_at, resolved_at: null },
        { id: 'a2', worker_id: 'worker-2', kind: 'worker_stale', created_at: operations.observed_at, resolved_at: operations.observed_at },
      ],
    }) });
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
});
