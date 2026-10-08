import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { DiscoveryProfile, DiscoveryProfilesValue, GlobalSetting } from '../../../models';
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
