import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import {
  DiscoveryProfile,
  DiscoveryProfilesValue,
  GlobalSetting,
  SourceReviewSettings,
} from '../../../models';
import { AdminService } from '../../../services/admin';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';
import { AdminSettings } from './admin-settings';

const profile: DiscoveryProfile = {
  id: 'science',
  name: '中文科學',
  language: 'zh',
  category: '科學',
  enabled: true,
  quota: 5,
  seed_urls: ['https://example.com'],
};

const reviewValue: SourceReviewSettings = {
  enabled: false,
  skip_human_review: false,
  engine: 'external_agent',
  model: 'jev-1.13.0',
  min_confidence: 0.9,
  min_quality: 0.7,
  max_spam_probability: 0.2,
  policy_text: 'Review information value.',
  daily_budget_usd: 1,
  sample_size: 5,
};

function setup(overrides: Record<string, unknown> = {}) {
  const writes: { key: string; value: DiscoveryProfilesValue; version: number }[] = [];
  const warnings: string[] = [];
  TestBed.configureTestingModule({
    imports: [AdminSettings],
    providers: [
      {
        provide: AdminService,
        useValue: {
          globalSettings: () =>
            of({
              settings: [
                { key: 'source.review', value: { ...reviewValue }, version: 2, updated_at: null },
                {
                  key: 'discovery.profiles',
                  value: { profiles: [{ ...profile }] },
                  version: 3,
                  updated_at: null,
                },
              ],
            }),
          saveGlobalSetting: (key: string, value: DiscoveryProfilesValue, version: number) => {
            writes.push({ key, value, version });
            return of({ key, value, version: 4, updated_at: null });
          },
          ...overrides,
        },
      },
      {
        provide: ToastService,
        useValue: { success: () => {}, warning: (message: string) => warnings.push(message) },
      },
      { provide: ConfirmService, useValue: { ask: () => Promise.resolve(true) } },
    ],
  });
  const fixture = TestBed.createComponent(AdminSettings);
  fixture.detectChanges();
  const component = fixture.componentInstance as unknown as {
    reviewSettings: SourceReviewSettings;
    reviewDirty: () => boolean;
    reviewConflict: () => boolean;
    reviewSaving: () => boolean;
    markReviewDirty: () => void;
    saveReviewSettings: () => void;
    load: () => Promise<void>;
    profiles: () => DiscoveryProfile[];
    dirty: () => boolean;
    conflict: () => boolean;
    saving: () => boolean;
    draft: { name: string; language: string; category: string; enabled: boolean; quota: number };
    seedUrls: string;
    edit: (entry: DiscoveryProfile) => void;
    applyDraft: () => void;
    toggle: (entry: DiscoveryProfile) => void;
    remove: (entry: DiscoveryProfile) => Promise<void>;
    saveSettings: () => void;
  };
  return { fixture, component, writes, warnings };
}

describe('AdminSettings', () => {
  it('allows explicitly applying unchanged defaults without writes during page load', () => {
    const { fixture, component, writes } = setup();
    expect(component.dirty()).toBe(false);
    expect(writes).toHaveLength(0);
    const button = fixture.nativeElement.querySelector('.save-bar button') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain('套用目前設定');
    button.click();
    expect(writes).toEqual([
      { key: 'discovery.profiles', value: { profiles: [profile] }, version: 3 },
    ]);
    expect(component.dirty()).toBe(false);
  });

  it('blocks duplicate pending saves and uses the returned version for the next explicit apply', () => {
    const responses: Subject<GlobalSetting<DiscoveryProfilesValue>>[] = [];
    const versions: number[] = [];
    const { component, fixture } = setup({
      saveGlobalSetting: (_key: string, _value: DiscoveryProfilesValue, version: number) => {
        versions.push(version);
        const response = new Subject<GlobalSetting<DiscoveryProfilesValue>>();
        responses.push(response);
        return response;
      },
    });
    component.saveSettings();
    component.saveSettings();
    fixture.detectChanges();
    expect(versions).toEqual([3]);
    expect(
      (fixture.nativeElement.querySelector('.save-bar button') as HTMLButtonElement).disabled,
    ).toBe(true);
    responses[0].next({
      key: 'discovery.profiles',
      value: { profiles: [profile] },
      version: 4,
      updated_at: null,
    });
    responses[0].complete();
    component.saveSettings();
    expect(versions).toEqual([3, 4]);
    responses[1].next({
      key: 'discovery.profiles',
      value: { profiles: [profile] },
      version: 5,
      updated_at: null,
    });
    expect(component.saving()).toBe(false);
  });

  it('blocks applying unchanged settings again after a version conflict', () => {
    let attempts = 0;
    const { component, fixture } = setup({
      saveGlobalSetting: () => {
        attempts++;
        return throwError(() => new HttpErrorResponse({ status: 409 }));
      },
    });
    component.saveSettings();
    component.saveSettings();
    fixture.detectChanges();
    expect(attempts).toBe(1);
    expect(component.profiles()).toEqual([profile]);
    expect(component.conflict()).toBe(true);
    expect(
      (fixture.nativeElement.querySelector('.save-bar button') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('edits a profile and saves the complete setting with its loaded version', () => {
    const { component, writes } = setup();
    component.edit(component.profiles()[0]);
    component.draft.name = '科學與健康';
    component.seedUrls = 'https://example.com\nhttps://another.example\nhttps://example.com';
    component.applyDraft();
    expect(component.profiles()[0].id).toBe('science');
    expect(component.dirty()).toBe(true);
    expect(writes).toHaveLength(0);
    component.saveSettings();
    expect(writes[0].version).toBe(3);
    expect(writes[0].key).toBe('discovery.profiles');
    expect(writes[0].value.profiles[0].name).toBe('科學與健康');
    expect(writes[0].value.profiles[0].seed_urls).toHaveLength(2);
    expect(component.dirty()).toBe(false);
  });

  it('disables locally and only persists when settings are saved', () => {
    const { component, writes } = setup();
    component.toggle(component.profiles()[0]);
    expect(component.profiles()[0].enabled).toBe(false);
    expect(writes).toHaveLength(0);
    component.saveSettings();
    expect(writes[0].value.profiles[0].enabled).toBe(false);
  });

  it('retains local edits after a version conflict and explains recovery', () => {
    const { component, fixture } = setup({
      saveGlobalSetting: () => throwError(() => new HttpErrorResponse({ status: 409 })),
    });
    component.toggle(component.profiles()[0]);
    component.saveSettings();
    fixture.detectChanges();
    expect(component.conflict()).toBe(true);
    expect(component.profiles()[0].enabled).toBe(false);
    expect(component.dirty()).toBe(true);
    expect(component.saving()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('你的變更已保留');
  });

  it('does not present a failed load as an empty configuration', () => {
    const { fixture } = setup({ globalSettings: () => throwError(() => new Error('offline')) });
    expect(fixture.nativeElement.textContent).toContain('無法讀取擷取方向');
    expect(fixture.nativeElement.textContent).not.toContain('尚未設定擷取方向');
  });

  it('rejects invalid URLs and fractional quotas before changing the list', () => {
    const { component, warnings } = setup();
    component.edit(component.profiles()[0]);
    component.draft.quota = 1.5;
    component.applyDraft();
    component.draft.quota = 5;
    component.seedUrls = 'javascript:alert(1)';
    component.applyDraft();
    expect(component.profiles()).toEqual([profile]);
    expect(component.dirty()).toBe(false);
    expect(warnings).toHaveLength(2);
  });

  it('rejects too many seeds and URLs carrying credentials', () => {
    const { component, warnings } = setup();
    component.edit(component.profiles()[0]);
    component.seedUrls = Array.from({ length: 31 }, (_, i) => `https://site${i}.example`).join(
      '\n',
    );
    component.applyDraft();
    component.seedUrls = 'https://user:secret@example.com';
    component.applyDraft();
    expect(component.dirty()).toBe(false);
    expect(warnings).toHaveLength(2);
  });

  it('removes a profile locally after confirmation', async () => {
    const { component, writes } = setup();
    await component.remove(component.profiles()[0]);
    expect(component.profiles()).toHaveLength(0);
    expect(writes).toHaveLength(0);
    component.saveSettings();
    expect(writes[0].value.profiles).toEqual([]);
  });
});

describe('AdminSettings source review', () => {
  it('saves independently with CAS and explains optional human review and runtime availability', () => {
    let saved: unknown;
    const { component, fixture } = setup({
      saveGlobalSetting: (key: string, value: SourceReviewSettings, version: number) => {
        saved = { key, value, version };
        return of({ key, value, version: 3, updated_at: null });
      },
    });
    component.reviewSettings.skip_human_review = true;
    component.reviewSettings.engine = 'jev';
    component.markReviewDirty();
    component.saveReviewSettings();
    expect(saved).toEqual({
      key: 'source.review',
      value: { ...reviewValue, skip_human_review: true, engine: 'jev' },
      version: 2,
    });
    expect(component.reviewDirty()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('既有待人工覆核的結果不會批次放行');
    expect(fixture.nativeElement.textContent).toContain('不受自動探索暫停或部署開關影響');
  });

  it('keeps the source review draft on conflict and blocks repeated writes', () => {
    let count = 0;
    const { component } = setup({
      saveGlobalSetting: () => {
        count++;
        return throwError(() => new HttpErrorResponse({ status: 409 }));
      },
    });
    component.reviewSettings.enabled = true;
    component.markReviewDirty();
    component.saveReviewSettings();
    component.saveReviewSettings();
    expect(count).toBe(1);
    expect(component.reviewSettings.enabled).toBe(true);
    expect(component.reviewDirty()).toBe(true);
    expect(component.reviewConflict()).toBe(true);
  });

  it('rejects invalid sample counts and thresholds before saving', () => {
    const { component, writes, warnings } = setup();
    component.reviewSettings.sample_size = 1.5;
    component.saveReviewSettings();
    component.reviewSettings.sample_size = 5;
    component.reviewSettings.min_quality = 1.1;
    component.saveReviewSettings();
    expect(writes).toHaveLength(0);
    expect(warnings).toHaveLength(2);
  });

  it('blocks reload and duplicate writes while saving, and cancels late responses on destruction', async () => {
    const response = new Subject<GlobalSetting<SourceReviewSettings>>();
    let count = 0;
    const { component, fixture } = setup({
      saveGlobalSetting: () => {
        count++;
        return response;
      },
    });
    component.reviewSettings.enabled = true;
    component.saveReviewSettings();
    component.saveReviewSettings();
    await component.load();
    expect(component.reviewSettings.enabled).toBe(true);
    expect(count).toBe(1);
    fixture.destroy();
    response.next({
      key: 'source.review',
      value: { ...reviewValue },
      version: 3,
      updated_at: null,
    });
    expect(component.reviewSettings.enabled).toBe(true);
  });
  it('ignores stale settings reads and uses the newest version for saving', async () => {
    const reads: Subject<{ settings: GlobalSetting[] }>[] = [];
    let version = -1;
    const { component } = setup({
      globalSettings: () => {
        const response = new Subject<{ settings: GlobalSetting[] }>();
        reads.push(response);
        return response;
      },
      saveGlobalSetting: (key: string, value: SourceReviewSettings, expected: number) => {
        version = expected;
        return of({ key, value, version: expected + 1, updated_at: null });
      },
    });
    await component.load();
    const settings = (enabled: boolean, version: number) => ({
      settings: [
        { key: 'discovery.profiles', value: { profiles: [profile] }, version: 3, updated_at: null },
        { key: 'source.review', value: { ...reviewValue, enabled }, version, updated_at: null },
      ],
    });
    reads[1].next(settings(true, 5));
    reads[0].next(settings(false, 2));
    expect(component.reviewSettings.enabled).toBe(true);
    component.saveReviewSettings();
    expect(version).toBe(5);
  });
  it('shows model selection only for Jev and explains the external agent model boundary', () => {
    const { component, fixture } = setup();
    expect(fixture.nativeElement.querySelector('[name="model"]')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('外部 Agent 自行選擇模型');
    component.reviewSettings.engine = 'jev';
    component.markReviewDirty();
    fixture.detectChanges();
    const model = fixture.nativeElement.querySelector('[name="model"]') as HTMLSelectElement;
    expect(model).not.toBeNull();
    expect(model.options.length).toBe(1);
    expect(model.options[0].value).toBe('jev-1.13.0');
    expect(fixture.nativeElement.textContent).toContain('目前支援 jev-1.13.0');
  });
});
