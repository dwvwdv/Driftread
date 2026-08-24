import { ChangeDetectionStrategy, Component, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../services/me';
import { FeedService } from '../../services/feed';
import { AuthService } from '../../services/auth';
import { apiMessage } from '../../shared/http-errors';
import { ObLoading, ObEmpty, ObError } from '../../ui/state/state';
import { ObPageHeader } from '../../ui/page-header/page-header';
import { ToastService } from '../../ui/toast/toast';

/**
 * Recommendation preferences: which categories and languages to weight
 * towards, wired to the existing GET/PUT /me/preferences pair that
 * routers/recommendations.py already reads (`_signals()`). Nothing here
 * introduces new backend state — this is only the UI TODO.md flagged as
 * missing.
 *
 * Category/language options come from GET /feeds/categories and the new
 * GET /feeds/languages (migration 014) — the catalog's actual vocabulary,
 * not a hardcoded list that would drift from it.
 */
@Component({
  selector: 'app-preferences',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, ObLoading, ObEmpty, ObError, ObPageHeader],
  templateUrl: './preferences.html',
  styleUrl: './preferences.scss',
})
export class Preferences {
  protected auth = inject(AuthService);
  private me = inject(MeService);
  private feedService = inject(FeedService);
  private toast = inject(ToastService);

  loading = signal(false);
  saving = signal(false);
  /**
   * Set only when GET /me/preferences itself fails — not when the catalog
   * options fail (those degrade to an empty chip list + a toast, since they
   * aren't destructive). While this is set, the template renders `ob-error`
   * instead of the form: the alternative is showing an editable, save-enabled
   * form seeded with two empty Sets, where clicking "儲存偏好" would silently
   * overwrite the reader's real preferences with an empty selection.
   */
  error = signal<string | null>(null);

  categoryOptions = signal<string[]>([]);
  languageOptions = signal<string[]>([]);
  selectedCategories = signal<Set<string>>(new Set());
  selectedLanguages = signal<Set<string>>(new Set());

  /** User id the options + current preferences have already been loaded for. */
  private loadedFor: string | null = null;

  /**
   * Bumped by every load() call. A response is only applied if its captured
   * generation still matches — otherwise a slower response from a load kicked
   * off for a previous account (or a stale manual retry) could land after a
   * newer load already started, and overwrite the new account's selections
   * with the old one's. Same pattern ReadingStreamService uses for its own
   * item/count generations.
   */
  private loadGeneration = 0;

  constructor() {
    // Same reason as bookmarks/my-feeds: AuthService restores the persisted
    // session asynchronously, so a one-shot check in ngOnInit would run before
    // the session exists on a direct visit to this route.
    effect(() => {
      const userId = this.auth.session()?.user?.id ?? null;
      if (!userId) {
        this.loadedFor = null;
        return;
      }
      if (this.loadedFor === userId) return;
      this.loadedFor = userId;
      this.load();
    });
  }

  load(): void {
    const generation = ++this.loadGeneration;
    this.loading.set(true);
    this.error.set(null);

    // Options and the saved selection are independent reads; run them
    // together rather than chaining, since neither depends on the other's
    // result.
    let pending = 3;
    const done = () => {
      pending -= 1;
      if (pending === 0 && generation === this.loadGeneration) this.loading.set(false);
    };

    this.feedService.getCategories().subscribe({
      next: (categories) => {
        if (generation === this.loadGeneration) this.categoryOptions.set(categories);
        done();
      },
      error: (e: unknown) => {
        if (generation === this.loadGeneration) this.toast.danger(apiMessage(e, '無法載入分類清單'));
        done();
      },
    });

    this.feedService.getLanguages().subscribe({
      next: (languages) => {
        if (generation === this.loadGeneration) this.languageOptions.set(languages);
        done();
      },
      error: (e: unknown) => {
        if (generation === this.loadGeneration) this.toast.danger(apiMessage(e, '無法載入語言清單'));
        done();
      },
    });

    this.me.getPreferences().subscribe({
      next: (prefs) => {
        if (generation === this.loadGeneration) {
          this.selectedCategories.set(new Set(prefs.preferred_categories));
          this.selectedLanguages.set(new Set(prefs.preferred_languages));
        }
        done();
      },
      error: (e: unknown) => {
        if (generation === this.loadGeneration) this.error.set(apiMessage(e, '無法載入偏好設定'));
        done();
      },
    });
  }

  toggleCategory(category: string): void {
    this.selectedCategories.update((current) => toggled(current, category));
  }

  toggleLanguage(language: string): void {
    this.selectedLanguages.update((current) => toggled(current, language));
  }

  save(): void {
    this.saving.set(true);
    this.me
      .updatePreferences({
        preferred_categories: [...this.selectedCategories()],
        preferred_languages: [...this.selectedLanguages()],
      })
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.toast.success('偏好設定已儲存');
        },
        error: (e: unknown) => {
          this.saving.set(false);
          this.toast.danger(apiMessage(e, '儲存失敗'));
        },
      });
  }
}

function toggled(set: Set<string>, value: string): Set<string> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}
