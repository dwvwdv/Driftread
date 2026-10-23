import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ArticleCapSettings, ArticleCapSummary, GlobalSetting } from '../../../models';
import { AdminService } from '../../../services/admin';
import { ObCard } from '../../../ui/card/card';
import { ConfirmService } from '../../../ui/confirm/confirm';
import { ToastService } from '../../../ui/toast/toast';

const CAP_KEY = 'article.cap';
const MIN_CAP = 100;
const MAX_CAP = 100000;

/**
 * Global per-source article cap (docs/CRAWLER_OPERATIONS.md「每來源文章上限」).
 * The worker re-reads the setting before each hourly batch. The UI only
 * previews; applying outside the schedule stays an explicit API call.
 */
@Component({
  selector: 'app-article-cap-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, ObCard],
  template: `
    <ob-card heading="每來源文章上限" meta="ARTICLE CAP">
      <p role="status">
        {{
          loading()
            ? '讀取設定中…'
            : !saved()
              ? '無法讀取文章上限設定'
              : saved()!.enabled
                ? '已啟用：每個來源最多保留 ' + saved()!.max_per_feed + ' 篇'
                : '已停用：排程不會刪除任何文章'
        }}
      </p>
      <p class="hint">
        超過上限時，每小時刪除最舊的文章（依發布時間，未來日期以首次抓取時間計）。收藏、稍後讀、人工事件引用與後台暫存的文章不刪除，也不計入上限；已讀紀錄會隨文章一併刪除。刪除無法還原，來源設定可個別覆寫上限。
      </p>
      @if (saved()) {
        <form (ngSubmit)="save()">
          <fieldset [disabled]="busy() || loading()">
            <label class="ob-checkbox"
              ><input type="checkbox" name="enabled" [(ngModel)]="draft.enabled" />啟用排程刪除</label
            >
            <label class="field"
              ><span>每個來源最多保留（篇）</span>
              <input
                class="ob-input"
                name="maxPerFeed"
                type="number"
                min="100"
                max="100000"
                step="1"
                required
                [(ngModel)]="draft.max_per_feed"
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
          以每來源 {{ p.cap }} 篇預覽（有個別覆寫的來源照其設定）：依快取篇數有
          {{ p.summary.feeds_over_cap }} 個來源超過上限、最多超出 {{ p.summary.excess }}
          篇（含受保護文章）；下一批會刪除 {{ p.summary.eligible }} 篇。
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
export class ArticleCapControl implements OnInit {
  private admin = inject(AdminService);
  private confirm = inject(ConfirmService);
  private toast = inject(ToastService);
  private destroyRef = inject(DestroyRef);
  protected saved = signal<ArticleCapSettings | null>(null);
  /** The preview runs against the draft cap, so it shows what saving would do. */
  protected preview$ = signal<{ cap: number; summary: ArticleCapSummary } | null>(null);
  protected loading = signal(false);
  protected busy = signal(false);
  protected draft: ArticleCapSettings = { enabled: true, max_per_feed: 1000 };
  private version = 0;
  // Separate counters: an action started while a reload is in flight must not
  // discard that reload (loading would stay true with a stale version).
  private loadSeq = 0;
  private actionSeq = 0;

  ngOnInit(): void {
    this.destroyRef.onDestroy(() => {
      ++this.loadSeq;
      ++this.actionSeq;
    });
    this.load();
  }

  /** keepDraft: after a failed save, show the stored value but keep the edit. */
  protected load(keepDraft = false): void {
    const seq = ++this.loadSeq;
    this.loading.set(true);
    this.admin.globalSettings().subscribe({
      next: (response) => {
        if (seq !== this.loadSeq) return;
        const setting = response.settings.find((entry) => entry.key === CAP_KEY) as
          | GlobalSetting<ArticleCapSettings>
          | undefined;
        this.apply(setting ?? null, keepDraft);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.loadSeq) return;
        this.apply(null, keepDraft);
        this.loading.set(false);
      },
    });
  }

  protected async save(): Promise<void> {
    const current = this.saved();
    const value = { enabled: this.draft.enabled, max_per_feed: this.draft.max_per_feed };
    if (!current || this.busy() || this.loading() || !this.validCap(value.max_per_feed)) return;
    // Turning deletion on, or lowering the cap, removes more articles.
    const deletesMore =
      value.enabled && (!current.enabled || value.max_per_feed < current.max_per_feed);
    if (deletesMore) {
      const ok = await this.confirm.ask({
        heading: `每個來源只保留 ${value.max_per_feed} 篇？`,
        body: '排程會逐批刪除超過上限的舊文章與其已讀紀錄，刪除後無法還原。收藏、稍後讀與人工事件引用的文章不受影響。',
        confirmLabel: '儲存',
      });
      if (!ok || this.destroyRef.destroyed) return;
    }
    const seq = ++this.actionSeq;
    this.busy.set(true);
    this.admin.saveGlobalSetting(CAP_KEY, value, this.version).subscribe({
      next: (setting) => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        this.preview$.set(null);
        this.apply(setting);
        this.toast.success('文章上限設定已儲存');
      },
      error: () => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        // A 409 means another administrator saved first: show their value
        // and version, keep this edit so saving again is a deliberate choice.
        this.load(true);
      },
    });
  }

  protected preview(): void {
    const cap = this.draft.max_per_feed;
    if (this.busy() || this.loading() || !this.validCap(cap)) return;
    const seq = ++this.actionSeq;
    this.busy.set(true);
    this.admin.runArticleCap(true, cap).subscribe({
      next: (summary) => {
        if (seq !== this.actionSeq) return;
        this.busy.set(false);
        this.preview$.set({ cap, summary });
      },
      error: () => {
        if (seq === this.actionSeq) this.busy.set(false);
      },
    });
  }

  private validCap(cap: number): boolean {
    if (Number.isInteger(cap) && cap >= MIN_CAP && cap <= MAX_CAP) return true;
    this.toast.warning(`上限須為 ${MIN_CAP}–${MAX_CAP} 的整數`);
    return false;
  }

  private apply(setting: GlobalSetting<ArticleCapSettings> | null, keepDraft = false): void {
    this.saved.set(setting ? { ...setting.value } : null);
    this.version = setting?.version ?? 0;
    if (setting && !keepDraft) this.draft = { ...setting.value };
  }
}
