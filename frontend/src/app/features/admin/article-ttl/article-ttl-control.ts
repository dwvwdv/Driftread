import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ArticleTtlSettings, ArticleTtlSummary, GlobalSetting } from '../../../models';
import { AdminService } from '../../../services/admin';
import { ObCard } from '../../../ui/card/card';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

const TTL_KEY = 'article.ttl';
const MIN_DAYS = 1;
const MAX_DAYS = 3650;

@Component({
  selector: 'app-article-ttl-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCard],
  template: `
    <ob-card heading="文章到期自動刪除" meta="ARTICLE TTL">
      <p role="status">
        {{
          loading()
            ? '讀取設定中…'
            : !saved()
              ? '無法讀取文章保存設定'
              : saved()!.enabled
                ? '已啟用：保存 ' + saved()!.days + ' 天，每小時最多刪除 500 篇'
                : '已停用：到期排程不會刪除文章'
        }}
      </p>
      <p class="hint">
        永久刪除超過保存天數的完整文章與其已讀紀錄。任何人的收藏、稍後讀、人工事件引用與後台暫存文章皆受保護；訂閱與已讀不會保護文章。依發布時間計算，缺少日期或未來日期以首次抓取時間計。刪除無法還原。
      </p>
      <p class="hint">這個開關獨立於每來源篇數上限、正文縮減、RSS 更新與自動探索。</p>
      <p class="hint">啟用期間不收錄已到期的歷史 RSS 項目；停用後可重新收錄歷史文章。</p>
      @if (saved()) {
        <form (ngSubmit)="save()">
          <fieldset [disabled]="busy() || loading()">
            <label class="ob-checkbox">
              <input type="checkbox" name="enabled" [(ngModel)]="draft.enabled" />啟用到期自動刪除
            </label>
            <label class="field">
              <span>保存天數</span>
              <input
                class="ob-input"
                name="days"
                type="number"
                min="1"
                max="3650"
                step="1"
                required
                [(ngModel)]="draft.days"
                (ngModelChange)="clearPreview()"
              />
            </label>
          </fieldset>
          <div class="actions">
            <button
              type="submit"
              class="ob-btn ob-btn--primary ob-btn--sm"
              [disabled]="busy() || loading()"
            >
              {{ busy() ? '處理中…' : '儲存' }}
            </button>
            <button
              type="button"
              class="ob-btn ob-btn--ghost ob-btn--sm"
              [disabled]="busy() || loading()"
              (click)="preview()"
            >
              預覽下一批
            </button>
          </div>
        </form>
      }
      @if (preview$(); as p) {
        <p class="hint" data-preview>
          以保存 {{ p.days }} 天預覽：共 {{ p.summary.expired }} 篇已到期，其中
          {{ p.summary.protected }} 篇受保護；下一批最多刪除 {{ p.summary.eligible }} 篇，影響
          {{ p.summary.reads_affected }} 筆已讀紀錄。預覽不會刪除資料，實際執行會重新檢查保護條件。
        </p>
        <div class="actions">
          <button
            type="button"
            class="ob-btn ob-btn--danger ob-btn--sm"
            [disabled]="busy() || loading() || !p.summary.eligible || p.days !== draft.days"
            (click)="applyBatch()"
          >
            確認並刪除這一批
          </button>
        </div>
      }
      @if (lastRun(); as run) {
        <p role="status" class="hint" data-result>
          本次刪除 {{ run.deleted }} 篇、{{ run.reads_affected }} 筆已讀紀錄，略過
          {{ run.skipped_feeds }} 個處理中的來源。
        </p>
      }
    </ob-card>
  `,
  styles: [
    `
      :host {
        display: block;
        margin-bottom: var(--sp-5);
      }
      .hint {
        color: var(--text-dim);
        font-size: var(--text-sm);
        margin-top: var(--sp-2);
      }
      fieldset {
        border: 0;
        padding: 0;
        margin: var(--sp-3) 0 0;
        display: grid;
        gap: var(--sp-3);
      }
      .field {
        display: grid;
        gap: var(--sp-1);
      }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sp-2);
        margin-top: var(--sp-3);
      }
    `,
  ],
})
export class ArticleTtlControl implements OnInit {
  private admin = inject(AdminService);
  private confirm = inject(ConfirmService);
  private toast = inject(ToastService);
  private destroyRef = inject(DestroyRef);
  protected saved = signal<ArticleTtlSettings | null>(null);
  protected preview$ = signal<{ days: number; summary: ArticleTtlSummary } | null>(null);
  protected lastRun = signal<ArticleTtlSummary | null>(null);
  protected loading = signal(false);
  protected busy = signal(false);
  protected draft: ArticleTtlSettings = { enabled: false, days: 14 };
  private version = 0;
  private loadSeq = 0;
  private actionSeq = 0;

  ngOnInit(): void {
    this.destroyRef.onDestroy(() => {
      ++this.loadSeq;
      ++this.actionSeq;
    });
    this.load();
  }

  /** Failed saves refresh the authoritative version while preserving the edit. */
  protected load(keepDraft = false): void {
    const seq = ++this.loadSeq;
    this.loading.set(true);
    this.clearPreview();
    this.admin.globalSettings().subscribe({
      next: (response) => {
        if (seq !== this.loadSeq) return;
        const setting = response.settings.find((entry) => entry.key === TTL_KEY) as
          | GlobalSetting<ArticleTtlSettings>
          | undefined;
        this.applySetting(setting ?? null, keepDraft);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.loadSeq) return;
        this.applySetting(null, keepDraft);
        this.loading.set(false);
      },
    });
  }

  protected clearPreview(): void {
    this.preview$.set(null);
  }

  protected async save(): Promise<void> {
    const current = this.saved();
    const value = { ...this.draft };
    if (!current || this.busy() || this.loading() || !this.validDays(value.days)) return;
    const seq = ++this.actionSeq;
    // Keep actions mutually exclusive while the confirmation is open too.
    this.busy.set(true);
    if (value.enabled && (!current.enabled || value.days < current.days)) {
      const ok = await this.confirm.ask({
        heading: `啟用保存 ${value.days} 天後自動刪除？`,
        body: '排程每小時最多永久刪除 500 篇到期文章與其已讀紀錄。收藏、稍後讀、人工事件引用與後台暫存文章受保護；訂閱與已讀不會保護文章。',
        confirmLabel: '儲存設定',
      });
      if (this.destroyRef.destroyed || seq !== this.actionSeq) return;
      if (!ok) {
        this.busy.set(false);
        return;
      }
    }
    this.admin.saveGlobalSetting(TTL_KEY, value, this.version).subscribe({
      next: (setting) => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        this.clearPreview();
        this.applySetting(setting);
        this.toast.success('文章保存設定已儲存');
      },
      error: () => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        this.load(true);
      },
    });
  }

  protected preview(): void {
    const days = this.draft.days;
    if (!this.saved() || this.busy() || this.loading() || !this.validDays(days)) return;
    const seq = ++this.actionSeq;
    this.busy.set(true);
    this.clearPreview();
    this.lastRun.set(null);
    this.admin.runArticleTtl(true, days).subscribe({
      next: (summary) => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        if (days === this.draft.days) this.preview$.set({ days, summary });
      },
      error: () => {
        if (seq === this.actionSeq) this.busy.set(false);
      },
    });
  }

  protected async applyBatch(): Promise<void> {
    const preview = this.preview$();
    if (
      !this.saved() ||
      this.busy() ||
      this.loading() ||
      !preview?.summary.eligible ||
      preview.days !== this.draft.days
    )
      return;
    const seq = ++this.actionSeq;
    this.busy.set(true);
    const ok = await this.confirm.ask({
      heading: `永久刪除最多 ${preview.summary.eligible} 篇到期文章？`,
      body: `本次依保存 ${preview.days} 天執行一批，預覽影響 ${preview.summary.reads_affected} 筆已讀紀錄。刪除無法還原；實際刪除前會重新檢查收藏與人工引用保護。此操作不會變更自動刪除開關。`,
      confirmLabel: '永久刪除這一批',
    });
    if (this.destroyRef.destroyed || seq !== this.actionSeq) return;
    if (!ok) {
      this.busy.set(false);
      return;
    }
    // A draft edit or refreshed preview while confirmation was open must not
    // make its approval apply to a different policy.
    if (preview !== this.preview$() || preview.days !== this.draft.days) {
      this.busy.set(false);
      return;
    }
    this.clearPreview();
    this.admin.runArticleTtl(false, preview.days, preview.summary.eligible).subscribe({
      next: (summary) => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        this.lastRun.set(summary);
        this.toast.success(`已刪除 ${summary.deleted} 篇到期文章`);
      },
      error: () => {
        if (seq === this.actionSeq) this.busy.set(false);
      },
    });
  }

  private validDays(days: number): boolean {
    if (Number.isInteger(days) && days >= MIN_DAYS && days <= MAX_DAYS) return true;
    this.toast.warning(`保存天數須為 ${MIN_DAYS}–${MAX_DAYS} 的整數`);
    return false;
  }

  private applySetting(setting: GlobalSetting<ArticleTtlSettings> | null, keepDraft = false): void {
    this.saved.set(setting ? { ...setting.value } : null);
    this.version = setting?.version ?? 0;
    if (setting && !keepDraft) this.draft = { ...setting.value };
  }
}
