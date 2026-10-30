import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { ArticleTtlControl } from './article-ttl-control';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { ArticleTtlSettings, ArticleTtlSummary, GlobalSetting } from '../../../models';

type Reader = {
  draft: ArticleTtlSettings;
  saved: () => ArticleTtlSettings | null;
  busy: () => boolean;
  loading: () => boolean;
  preview$: () => { days: number; summary: ArticleTtlSummary } | null;
  save: () => Promise<void>;
  preview: () => void;
  applyBatch: () => Promise<void>;
  clearPreview: () => void;
};

function setting(value: ArticleTtlSettings, version = 3): GlobalSetting<ArticleTtlSettings> {
  return { key: 'article.ttl', value, version, updated_at: null };
}

function summary(dryRun = true): ArticleTtlSummary {
  return {
    dry_run: dryRun,
    expired: 810,
    protected: 10,
    eligible: 500,
    deleted: dryRun ? 0 : 499,
    reads_affected: 3,
    skipped_feeds: 1,
    deletion_delay_seconds: dryRun ? 0 : 7200,
  };
}

function setup(
  options: {
    stored?: GlobalSetting<ArticleTtlSettings>;
    confirm?: boolean;
    ask?: () => Promise<boolean>;
    save?: () => Observable<GlobalSetting<ArticleTtlSettings>>;
    reload?: () => Observable<{ settings: GlobalSetting[] }>;
    run?: (dryRun: boolean) => Observable<ArticleTtlSummary>;
  } = {},
) {
  const stored = options.stored ?? setting({ enabled: false, days: 14 });
  const saves: { key: string; value: unknown; version: number }[] = [];
  const asks: unknown[] = [];
  const warnings: string[] = [];
  const runs: { dryRun: boolean; days?: number; limit?: number }[] = [];
  let loads = 0;
  TestBed.configureTestingModule({
    imports: [ArticleTtlControl],
    providers: [
      {
        provide: AdminService,
        useValue: {
          globalSettings: () => {
            loads++;
            if (loads > 1 && options.reload) return options.reload();
            return of({ settings: [stored] });
          },
          saveGlobalSetting: (key: string, value: ArticleTtlSettings, version: number) => {
            saves.push({ key, value, version });
            return options.save ? options.save() : of(setting(value, version + 1));
          },
          runArticleTtl: (dryRun: boolean, days?: number, limit?: number) => {
            runs.push(limit === undefined ? { dryRun, days } : { dryRun, days, limit });
            return options.run ? options.run(dryRun) : of(summary(dryRun));
          },
        },
      },
      {
        provide: ConfirmService,
        useValue: {
          ask: (o: unknown) => {
            asks.push(o);
            return options.ask ? options.ask() : Promise.resolve(options.confirm ?? true);
          },
        },
      },
      {
        provide: ToastService,
        useValue: { success: () => {}, warning: (m: string) => warnings.push(m) },
      },
    ],
  });
  const fixture = TestBed.createComponent(ArticleTtlControl);
  fixture.detectChanges();
  const reader = fixture.componentInstance as unknown as Reader;
  return { fixture, reader, saves, asks, warnings, runs, loads: () => loads };
}

describe('ArticleTtlControl', () => {
  it('starts disabled and explains whole-article deletion and protection', () => {
    const { fixture, reader } = setup();
    const text = fixture.nativeElement.textContent;
    expect(reader.draft).toEqual({ enabled: false, days: 14 });
    expect(text).toContain('已停用：到期排程不會刪除文章');
    expect(text).toContain('訂閱與已讀不會保護文章');
    expect(text).toContain('稍後讀、人工事件引用與後台暫存文章皆受保護');
  });

  it('previews draft days without saving or deleting and reports protected/read counts', () => {
    const { fixture, reader, saves, runs } = setup();
    reader.draft.days = 30;
    reader.preview();
    fixture.detectChanges();
    expect(saves).toEqual([]);
    expect(runs).toEqual([{ dryRun: true, days: 30 }]);
    const text = fixture.nativeElement.querySelector('[data-preview]').textContent;
    expect(text).toContain('以保存 30 天預覽');
    expect(text).toContain('810 篇已到期');
    expect(text).toContain('10 篇受保護');
    expect(text).toContain('500 篇');
    expect(text).toContain('3 筆已讀紀錄');
  });

  it('requires confirmation when enabling or reducing an enabled retention period', async () => {
    const { reader, saves, asks } = setup();
    reader.draft.enabled = true;
    await reader.save();
    reader.draft.days = 7;
    await reader.save();
    expect(asks.length).toBe(2);
    expect(saves).toEqual([
      { key: 'article.ttl', value: { enabled: true, days: 14 }, version: 3 },
      { key: 'article.ttl', value: { enabled: true, days: 7 }, version: 4 },
    ]);
  });

  it('does not save a declined confirmation', async () => {
    const { reader, saves } = setup({ confirm: false });
    reader.draft.enabled = true;
    await reader.save();
    expect(saves).toEqual([]);
    expect(reader.busy()).toBe(false);
  });

  it('disables and increases days without destructive confirmation', async () => {
    const { reader, saves, asks } = setup({ stored: setting({ enabled: true, days: 14 }) });
    reader.draft.days = 30;
    await reader.save();
    reader.draft.enabled = false;
    await reader.save();
    expect(asks).toEqual([]);
    expect(saves.length).toBe(2);
  });

  it('rejects invalid days for saving and preview', async () => {
    const { reader, saves, runs, warnings } = setup();
    for (const days of [0, 3651, 1.5, NaN]) {
      reader.draft.days = days;
      await reader.save();
      reader.preview();
    }
    expect(saves).toEqual([]);
    expect(runs).toEqual([]);
    expect(warnings.length).toBe(8);
  });

  it('reloads the authoritative version after save failure and preserves the edit', async () => {
    const pending = new Subject<{ settings: GlobalSetting[] }>();
    const { reader, saves, runs, loads } = setup({
      save: () => throwError(() => new Error('409')),
      reload: () => pending,
    });
    reader.draft = { enabled: false, days: 28 };
    await reader.save();
    expect(loads()).toBe(2);
    expect(reader.loading()).toBe(true);
    reader.preview();
    await reader.save();
    expect(runs).toEqual([]);
    expect(saves.length).toBe(1);
    pending.next({ settings: [setting({ enabled: true, days: 21 }, 9)] });
    expect(reader.loading()).toBe(false);
    expect(reader.saved()).toEqual({ enabled: true, days: 21 });
    expect(reader.draft).toEqual({ enabled: false, days: 28 });
    await reader.save();
    expect(saves.at(-1)?.version).toBe(9);
  });

  it('requires a matching preview and separate confirmation before manual apply', async () => {
    const { fixture, reader, runs, asks, saves } = setup();
    await reader.applyBatch();
    expect(runs).toEqual([]);
    reader.preview();
    await reader.applyBatch();
    fixture.detectChanges();
    expect(runs).toEqual([
      { dryRun: true, days: 14 },
      { dryRun: false, days: 14, limit: 500 },
    ]);
    expect(asks.length).toBe(1);
    expect(saves).toEqual([]);
    expect(reader.preview$()).toBe(null);
    expect(fixture.nativeElement.querySelector('[data-result]').textContent).toContain(
      '本次刪除 499 篇',
    );
  });

  it('does not manually delete when confirmation is declined', async () => {
    const { reader, runs } = setup({ confirm: false });
    reader.preview();
    await reader.applyBatch();
    expect(runs).toEqual([{ dryRun: true, days: 14 }]);
    expect(reader.busy()).toBe(false);
  });

  it('caps manual deletion at the approved preview count even when more articles can expire', async () => {
    const { reader, runs, asks } = setup({
      run: (dryRun) => of({ ...summary(dryRun), eligible: 3, deleted: dryRun ? 0 : 3 }),
    });
    reader.preview();
    await reader.applyBatch();
    expect(asks[0]).toEqual(expect.objectContaining({ heading: '永久刪除最多 3 篇到期文章？' }));
    expect(runs).toEqual([
      { dryRun: true, days: 14 },
      { dryRun: false, days: 14, limit: 3 },
    ]);
  });

  it('rejects a preview for a different day count', async () => {
    const { reader, runs } = setup();
    reader.preview();
    reader.draft.days = 7;
    await reader.applyBatch();
    expect(runs).toEqual([{ dryRun: true, days: 14 }]);
    reader.clearPreview();
    expect(reader.preview$()).toBe(null);
  });

  it('does not apply a policy changed while confirmation is open or start competing actions', async () => {
    let confirm!: (value: boolean) => void;
    const { reader, runs, saves } = setup({
      ask: () =>
        new Promise((resolve) => {
          confirm = resolve;
        }),
    });
    reader.preview();
    const pending = reader.applyBatch();
    expect(reader.busy()).toBe(true);
    reader.preview();
    await reader.save();
    reader.draft.days = 7;
    confirm(true);
    await pending;
    expect(runs).toEqual([{ dryRun: true, days: 14 }]);
    expect(saves).toEqual([]);
    expect(reader.busy()).toBe(false);
  });

  it('discards a late preview after the draft changed', () => {
    const pending = new Subject<ArticleTtlSummary>();
    const { reader } = setup({ run: () => pending });
    reader.preview();
    reader.draft.days = 7;
    pending.next(summary());
    expect(reader.busy()).toBe(false);
    expect(reader.preview$()).toBe(null);
  });

  it('clears a consumed preview even if applying fails, requiring a new preview', async () => {
    const { reader, runs } = setup({
      run: (dryRun) => (dryRun ? of(summary()) : throwError(() => new Error('offline'))),
    });
    reader.preview();
    await reader.applyBatch();
    await reader.applyBatch();
    expect(reader.preview$()).toBe(null);
    expect(reader.busy()).toBe(false);
    expect(runs.length).toBe(2);
  });

  it('ignores a preview response after the component is destroyed', () => {
    const pending = new Subject<ArticleTtlSummary>();
    const { fixture, reader } = setup({ run: () => pending });
    reader.preview();
    fixture.destroy();
    pending.next(summary());
    expect(reader.preview$()).toBe(null);
  });
});
