import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { StorageInsights } from './storage-insights';
import { AdminService } from '../../../services/admin';
import { IngestActivity, SourceActivity, TableStat, TableStorageHistory } from '../../../models';

const MiB = 1024 * 1024;

function source(overrides: Partial<SourceActivity>): SourceActivity {
  return {
    feed_id: 'f',
    title: 'Source',
    url: 'https://source.example/rss',
    archived: false,
    new_articles: 0,
    backfill_articles: 0,
    revision_updates: 0,
    revised_articles: 0,
    content_changes: 0,
    metadata_only_changes: 0,
    revisions_per_article: null,
    alerts: [],
    ...overrides,
  };
}

const activity: IngestActivity = {
  observed_at: '2026-10-21T08:00:00Z',
  since: '2026-10-20T08:00:00Z',
  window_hours: 24,
  thresholds: {
    new_articles: 500,
    revision_updates: 200,
    revisions_per_article: 3,
    revisions_per_article_min_updates: 30,
  },
  top_ingest: [
    source({
      feed_id: 'dev',
      title: 'DEV Community',
      new_articles: 645,
      backfill_articles: 12,
      alerts: ['new_articles'],
    }),
  ],
  top_revisions: [
    source({
      feed_id: 'kt',
      title: '<b>Korea Times</b>',
      revision_updates: 1589,
      revised_articles: 160,
      revisions_per_article: 9.93,
      content_changes: 3,
      metadata_only_changes: 1586,
      alerts: ['revision_updates', 'revisions_per_article'],
    }),
  ],
  flagged: [
    source({ feed_id: 'dev', title: 'DEV Community', new_articles: 645, alerts: ['new_articles'] }),
    source({
      feed_id: 'churn',
      title: 'Churn Feed',
      revision_updates: 40,
      revised_articles: 10,
      revisions_per_article: 4,
      alerts: ['revisions_per_article'],
    }),
  ],
  flagged_sources: 3,
};

const articles: TableStat = {
  name: 'articles',
  heap_bytes: 100 * MiB,
  toast_bytes: 200 * MiB,
  index_bytes: 50 * MiB,
  total_bytes: 350 * MiB,
  n_live_tup: 90000,
  n_dead_tup: 10000,
  n_tup_ins: 1,
  n_tup_upd: 1,
  n_tup_del: 0,
  n_tup_hot_upd: 0,
  last_vacuum: null,
  last_autovacuum: null,
  vacuum_count: 0,
  autovacuum_count: 140,
  dead_ratio: 0.1,
};

const tables: TableStorageHistory = {
  observed_at: '2026-10-21T08:00:00Z',
  retention_days: 90,
  current: { observed_at: '2026-10-21T08:00:00Z', stats_reset: null, tables: [articles] },
  intervals: [
    {
      start_at: '2026-10-20T00:05:00Z',
      end_at: '2026-10-21T00:05:00Z',
      elapsed_hours: 24,
      stats_reset_changed: false,
      tables: [
        {
          name: 'articles',
          comparable: true,
          inserted: 3989,
          updated: 12000,
          deleted: 0,
          hot_updated: 4000,
          total_bytes_delta: -2 * MiB,
          n_dead_tup: 10000,
        },
        {
          name: 'sync_changes',
          comparable: false,
          inserted: null,
          updated: null,
          deleted: null,
          hot_updated: null,
          total_bytes_delta: null,
          n_dead_tup: 3,
        },
      ],
    },
  ],
};

function render(overrides: Record<string, unknown> = {}) {
  TestBed.configureTestingModule({
    imports: [StorageInsights],
    providers: [
      {
        provide: AdminService,
        useValue: {
          ingestActivity: () => of(activity),
          tableStorage: () => of(tables),
          ...overrides,
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(StorageInsights);
  fixture.detectChanges();
  return fixture;
}

describe('StorageInsights', () => {
  it('lists top sources, thresholds and flags without offering any action', () => {
    const element: HTMLElement = render().nativeElement;
    const text = element.textContent!;
    expect(text).toContain('3 個來源超過觀察門檻');
    // A per-article churn source outside both top lists is still named.
    const flagged = element.querySelector('ul.flagged')!.textContent!;
    expect(flagged).toContain('Churn Feed：每篇平均版本偏高');
    expect(flagged).toContain('DEV Community：新增量偏高');
    expect(text).toContain('僅列出前 2 個');
    expect(text).toContain('新增 645 篇，其中舊文回填 12 篇');
    expect(text).toContain('新增量偏高');
    expect(text).toContain('更新 1,589 次／160 篇');
    expect(text).toContain('平均 9.9 次');
    expect(text).toContain('版本更新偏多、每篇平均版本偏高');
    expect(text).toContain('不會節流、限額或封存');
    // Third-party titles are text, never markup.
    expect(text).toContain('<b>Korea Times</b>');
    expect(element.querySelector('.sources b')).toBeNull();
    // The only buttons are the read-only window tabs.
    expect(element.querySelector('button:not([role="tab"])')).toBeNull();
  });

  it('shows per-table sizes and marks reset counters unknown instead of negative', () => {
    const element: HTMLElement = render().nativeElement;
    const [current, deltas] = Array.from(element.querySelectorAll('table'));
    expect(current.textContent).toContain('200.0 MiB');
    expect(current.textContent).toContain('10.0%');
    expect(current.textContent).toContain('尚無');
    const rows = Array.from(deltas.querySelectorAll('tbody tr')).map((row) => row.textContent!);
    expect(rows[0]).toContain('3,989');
    expect(rows[0]).toContain('-2.0 MiB');
    expect(rows[1]).toContain('無法計算');
    expect(rows[1]).not.toContain('-');
    expect(element.querySelector('details')!.open).toBe(true);
    for (const region of Array.from(element.querySelectorAll('.scroll'))) {
      expect(region.getAttribute('role')).toBe('region');
      expect(region.getAttribute('tabindex')).toBe('0');
    }
  });

  it('separates empty windows, missing intervals and failures per panel', () => {
    const empty = render({
      ingestActivity: () =>
        of({ ...activity, top_ingest: [], top_revisions: [], flagged: [], flagged_sources: 0 }),
      tableStorage: () => of({ ...tables, intervals: [] }),
    }).nativeElement.textContent;
    expect(empty).toContain('最近 24 小時沒有新增文章');
    expect(empty).toContain('最近 24 小時沒有文章版本更新');
    expect(empty).not.toContain('超過觀察門檻');
    expect(empty).toContain('區間增量需要兩筆每日採樣');
    TestBed.resetTestingModule();
    const failed = render({
      tableStorage: () => throwError(() => new Error('missing migration')),
    }).nativeElement.textContent;
    expect(failed).toContain('每表容量未知');
    expect(failed).toContain('DEV Community');
  });

  it('switches to the 7-day window without reloading the table panel', () => {
    const calls: number[] = [];
    const responses: Subject<IngestActivity>[] = [];
    let tableCalls = 0;
    const fixture = render({
      ingestActivity: (_limit: number, hours: number) => {
        calls.push(hours);
        const response = new Subject<IngestActivity>();
        responses.push(response);
        return response;
      },
      tableStorage: () => {
        tableCalls++;
        return of(tables);
      },
    });
    responses[0].next({ ...activity, flagged: [], flagged_sources: 0 });
    fixture.detectChanges();
    const element: HTMLElement = fixture.nativeElement;
    const tabs = element.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    expect(Array.from(tabs).map((t) => t.textContent!.trim())).toEqual(['24 小時', '7 天']);

    tabs[1].click();
    fixture.detectChanges();
    expect(calls).toEqual([24, 168]);
    expect(tableCalls).toBe(1);
    expect(element.textContent).toContain('LAST 7D');
    // The 24-hour answer arriving late must not overwrite the 7-day window.
    responses[0].next({ ...activity });
    responses[1].next({
      ...activity,
      window_hours: 168,
      top_ingest: [],
      top_revisions: [],
      flagged: [],
      flagged_sources: 0,
    });
    fixture.detectChanges();
    expect(element.textContent).toContain('最近 7 天沒有新增文章');
    expect(element.textContent).toContain('最近 168 小時');
    expect(element.textContent).not.toContain('超過觀察門檻');

    // Selecting the active tab again does not refetch.
    element.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1].click();
    expect(calls).toEqual([24, 168]);
  });

  it('drops a stale response after a reload', () => {
    const responses: Subject<IngestActivity>[] = [];
    const fixture = render({
      ingestActivity: () => {
        const response = new Subject<IngestActivity>();
        responses.push(response);
        return response;
      },
    });
    fixture.componentInstance.load();
    responses[1].next({ ...activity, flagged: [], flagged_sources: 0 });
    responses[0].error(new Error('old request'));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).not.toContain('來源入庫量未知');
    expect(fixture.nativeElement.textContent).not.toContain('超過觀察門檻');
  });
});
