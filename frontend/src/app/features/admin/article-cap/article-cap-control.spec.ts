import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { ArticleCapControl } from './article-cap-control';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { ArticleCapSettings, ArticleCapSummary, GlobalSetting } from '../../../models';

type Reader = {
  draft: ArticleCapSettings;
  saved: () => ArticleCapSettings | null;
  busy: () => boolean;
  save: () => Promise<void>;
  preview: () => void;
};

function setting(value: ArticleCapSettings, version = 3): GlobalSetting<ArticleCapSettings> {
  return { key: 'article.cap', value, version, updated_at: null };
}

function setup(options: {
  stored?: GlobalSetting<ArticleCapSettings>;
  confirm?: boolean;
  save?: () => Observable<GlobalSetting<ArticleCapSettings>>;
} = {}) {
  const stored = options.stored ?? setting({ enabled: true, max_per_feed: 1000 });
  const saves: { key: string; value: unknown; version: number }[] = [];
  const asks: unknown[] = [];
  const warnings: string[] = [];
  let loads = 0;
  const admin = {
    globalSettings: () => {
      loads++;
      return of({ settings: [{ key: 'source.review', value: {}, version: 1, updated_at: null }, stored] });
    },
    saveGlobalSetting: (key: string, value: ArticleCapSettings, version: number) => {
      saves.push({ key, value, version });
      return options.save ? options.save() : of(setting(value, version + 1));
    },
    runArticleCap: (dryRun: boolean) =>
      of<ArticleCapSummary>({
        dry_run: dryRun, feeds_over_cap: 8, excess: 8680, eligible: 500, deleted: 0, skipped_feeds: 0,
      }),
  };
  TestBed.configureTestingModule({
    imports: [ArticleCapControl],
    providers: [
      { provide: AdminService, useValue: admin },
      {
        provide: ConfirmService,
        useValue: { ask: (o: unknown) => (asks.push(o), Promise.resolve(options.confirm ?? true)) },
      },
      {
        provide: ToastService,
        useValue: { success: () => {}, warning: (m: string) => warnings.push(m) },
      },
    ],
  });
  const fixture = TestBed.createComponent(ArticleCapControl);
  fixture.detectChanges();
  const reader = fixture.componentInstance as unknown as Reader;
  return { fixture, reader, saves, asks, warnings, loads: () => loads };
}

describe('ArticleCapControl', () => {
  it('shows the stored cap and previews without deleting', () => {
    const { fixture, reader } = setup();
    expect(fixture.nativeElement.textContent).toContain('每個來源最多保留 1000 篇');
    reader.preview();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-preview]').textContent).toContain(
      '8 個來源超過上限',
    );
  });

  it('confirms before lowering the cap and saves with the loaded version', async () => {
    const { reader, saves, asks } = setup();
    reader.draft = { enabled: true, max_per_feed: 300 };
    await reader.save();
    expect(asks.length).toBe(1);
    expect(saves).toEqual([
      { key: 'article.cap', value: { enabled: true, max_per_feed: 300 }, version: 3 },
    ]);
    expect(reader.saved()).toEqual({ enabled: true, max_per_feed: 300 });
  });

  it('does not save when the confirmation is declined', async () => {
    const { reader, saves } = setup({ confirm: false });
    reader.draft = { enabled: true, max_per_feed: 300 };
    await reader.save();
    expect(saves).toEqual([]);
  });

  it('disabling or raising the cap needs no confirmation', async () => {
    const { reader, saves, asks } = setup();
    reader.draft = { enabled: false, max_per_feed: 1000 };
    await reader.save();
    reader.draft = { enabled: false, max_per_feed: 5000 };
    await reader.save();
    expect(asks).toEqual([]);
    expect(saves.map((s) => s.version)).toEqual([3, 4]);
  });

  it('confirms before turning deletion back on', async () => {
    const { reader, asks } = setup({ stored: setting({ enabled: false, max_per_feed: 1000 }) });
    reader.draft = { enabled: true, max_per_feed: 1000 };
    await reader.save();
    expect(asks.length).toBe(1);
  });

  it('rejects a cap the API would refuse', async () => {
    const { reader, saves, warnings } = setup();
    for (const max of [99, 100001, 1.5]) {
      reader.draft = { enabled: true, max_per_feed: max };
      await reader.save();
    }
    expect(saves).toEqual([]);
    expect(warnings.length).toBe(3);
  });

  it('reloads the stored version after a failed save but keeps the edit', async () => {
    const { reader, loads } = setup({ save: () => throwError(() => new Error('409')) });
    reader.draft = { enabled: false, max_per_feed: 1000 };
    await reader.save();
    expect(loads()).toBe(2);
    expect(reader.busy()).toBe(false);
    expect(reader.saved()).toEqual({ enabled: true, max_per_feed: 1000 });
    expect(reader.draft).toEqual({ enabled: false, max_per_feed: 1000 });
  });
});
