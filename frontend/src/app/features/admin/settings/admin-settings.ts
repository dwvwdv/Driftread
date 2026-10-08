import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DiscoveryProfile, DiscoveryProfilesValue, GlobalSetting } from '../../../models';
import { AdminService } from '../../../services/admin';
import { ObCard } from '../../../ui/card/card';
import { ObLoading, ObEmpty } from '../../../ui/state/state';
import { ObPageHeader } from '../../../ui/page-header/page-header';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

const PROFILE_KEY = 'discovery.profiles';
function emptyDraft() {
  return { name: '', language: 'zh', category: '', enabled: true, quota: 1 };
}

@Component({
  selector: 'app-admin-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCard, ObLoading, ObEmpty, ObPageHeader],
  templateUrl: './admin-settings.html',
  styleUrl: './admin-settings.scss',
})
export class AdminSettings implements OnInit {
  private admin = inject(AdminService);
  private toast = inject(ToastService);
  private confirm = inject(ConfirmService);
  protected profiles = signal<DiscoveryProfile[]>([]);
  protected loading = signal(true);
  protected loadFailed = signal(false);
  protected saving = signal(false);
  protected dirty = signal(false);
  protected conflict = signal(false);
  protected editingId: string | null = null;
  protected draft = emptyDraft();
  protected seedUrls = '';
  private version = 0;

  ngOnInit(): void {
    this.fetchSettings();
  }

  private fetchSettings(): void {
    this.loading.set(true);
    this.loadFailed.set(false);
    this.admin.globalSettings().subscribe({
      next: (response) => {
        const setting = response.settings.find((entry) => entry.key === PROFILE_KEY) as
          | GlobalSetting<DiscoveryProfilesValue>
          | undefined;
        if (!setting || !Array.isArray(setting.value?.profiles)) {
          this.loadFailed.set(true);
          this.loading.set(false);
          return;
        }
        this.profiles.set(
          setting.value.profiles.map((profile) => ({
            ...profile,
            seed_urls: [...profile.seed_urls],
          })),
        );
        this.version = setting.version;
        this.dirty.set(false);
        this.conflict.set(false);
        this.resetForm();
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.loadFailed.set(true);
      },
    });
  }

  protected async load(): Promise<void> {
    if (this.dirty() || this.editingId || this.draft.name || this.seedUrls) {
      const accepted = await this.confirm.ask({
        heading: '重新載入設定？',
        body: '尚未儲存的變更與表單內容將被放棄。',
        confirmLabel: '重新載入',
      });
      if (!accepted) return;
    }
    this.fetchSettings();
  }

  protected resetForm(): void {
    this.editingId = null;
    this.draft = emptyDraft();
    this.seedUrls = '';
  }

  protected edit(profile: DiscoveryProfile): void {
    this.editingId = profile.id;
    this.draft = { ...profile, category: profile.category ?? '' };
    this.seedUrls = profile.seed_urls.join('\n');
  }

  protected applyDraft(): void {
    const urls = [
      ...new Set(
        this.seedUrls
          .split('\n')
          .map((url) => url.trim())
          .filter(Boolean),
      ),
    ];
    const { name, language, quota } = this.draft;
    if (
      !name.trim() ||
      name.length > 100 ||
      this.draft.category.length > 100 ||
      /[\u0000-\u001f]/.test(name + this.draft.category) ||
      language.trim().length > 35 ||
      !/^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(language.trim()) ||
      !Number.isInteger(quota) ||
      quota < 0 ||
      quota > 100
    ) {
      this.toast.warning('請填寫名稱、有效語言代碼，以及 0–100 的整數名額');
      return;
    }
    if (
      urls.length > 30 ||
      urls.some((url) => {
        try {
          const parsed = new URL(url);
          return (
            url.length > 2048 ||
            !['http:', 'https:'].includes(parsed.protocol) ||
            !!parsed.username ||
            !!parsed.password
          );
        } catch {
          return true;
        }
      })
    ) {
      this.toast.warning('起始網站須為 http 或 https 網址，最多 30 個，不可包含帳號密碼');
      return;
    }
    if (
      (!this.editingId && this.profiles().length >= 30) ||
      this.profiles()
        .filter((entry) => entry.id !== this.editingId)
        .reduce((count, entry) => count + entry.seed_urls.length, urls.length) > 200
    ) {
      this.toast.warning('最多 30 個擷取方向，所有方向合計最多 200 個起始網站');
      return;
    }
    const profile: DiscoveryProfile = {
      id: this.editingId ?? crypto.randomUUID(),
      name: name.trim(),
      language: language.trim().toLowerCase(),
      category: this.draft.category.trim() || null,
      quota,
      enabled: this.draft.enabled,
      seed_urls: urls,
    };
    this.profiles.update((profiles) =>
      this.editingId
        ? profiles.map((entry) => (entry.id === this.editingId ? profile : entry))
        : [...profiles, profile],
    );
    this.dirty.set(true);
    this.resetForm();
  }

  protected toggle(profile: DiscoveryProfile): void {
    this.profiles.update((profiles) =>
      profiles.map((entry) =>
        entry.id === profile.id ? { ...entry, enabled: !entry.enabled } : entry,
      ),
    );
    if (this.editingId === profile.id) this.draft.enabled = !profile.enabled;
    this.dirty.set(true);
  }

  protected async remove(profile: DiscoveryProfile): Promise<void> {
    const accepted = await this.confirm.ask({
      heading: `刪除「${profile.name}」？`,
      body: '儲存後將移除此擷取方向。已加入的信息源與文章會保留。',
      confirmLabel: '刪除方向',
      danger: true,
    });
    if (!accepted) return;
    this.profiles.update((profiles) => profiles.filter((entry) => entry.id !== profile.id));
    if (this.editingId === profile.id) this.resetForm();
    this.dirty.set(true);
  }

  protected saveSettings(): void {
    if (this.editingId || this.draft.name || this.seedUrls) {
      this.toast.warning('請先將表單變更加入清單，或取消編輯');
      return;
    }
    if (!this.dirty() || this.saving() || this.loading() || this.loadFailed() || this.conflict())
      return;
    this.saving.set(true);
    this.admin
      .saveGlobalSetting(PROFILE_KEY, { profiles: this.profiles() }, this.version)
      .subscribe({
        next: (setting) => {
          this.profiles.set(setting.value.profiles);
          this.version = setting.version;
          this.dirty.set(false);
          this.conflict.set(false);
          this.saving.set(false);
          this.toast.success('全域設定已儲存');
        },
        error: (error: unknown) => {
          this.saving.set(false);
          if (error instanceof HttpErrorResponse && error.status === 409) this.conflict.set(true);
        },
      });
  }
}
