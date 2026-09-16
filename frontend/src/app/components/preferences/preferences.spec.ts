import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, Subject, of, throwError } from 'rxjs';
import { Preferences } from './preferences';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { FeedService } from '../../services/feed';
import { ToastService } from '../../ui/toast/toast';
import { UserPreferences } from '../../models';

describe('Preferences', () => {
  let updateCall: Subject<UserPreferences>;
  /** Every payload PUT /me/preferences was actually handed, in order. */
  let updateCalls: UserPreferences[];
  let toast: {
    info: ReturnType<typeof vi.fn>;
    danger: ReturnType<typeof vi.fn>;
    success: ReturnType<typeof vi.fn>;
    warning: ReturnType<typeof vi.fn>;
  };

  /**
   * `catalog` overrides GET /feeds/categories / GET /feeds/languages, which
   * the component treats very differently from GET /me/preferences: a failed
   * catalog read is non-destructive and only costs a toast plus an empty chip
   * list, while a failed preferences read hides the form entirely.
   */
  function setup(
    saved: UserPreferences = { preferred_categories: [], preferred_languages: [] },
    catalog: { categories?: Observable<string[]>; languages?: Observable<string[]> } = {},
  ) {
    updateCall = new Subject<UserPreferences>();
    updateCalls = [];
    toast = {
      info: vi.fn(),
      danger: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    };

    const me = {
      getPreferences: () => of(saved),
      updatePreferences: (prefs: UserPreferences) => {
        updateCalls.push(prefs);
        return updateCall;
      },
    };
    const feedService = {
      getCategories: () => catalog.categories ?? of(['News', 'Tech']),
      getLanguages: () => catalog.languages ?? of(['en', 'zh-TW']),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Preferences],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session: () => ({ user: { id: 'user-1' } }) } },
        { provide: MeService, useValue: me },
        { provide: FeedService, useValue: feedService },
        { provide: ToastService, useValue: toast },
      ],
    });

    const fixture = TestBed.createComponent(Preferences);
    fixture.detectChanges();
    return fixture.componentInstance;
  }

  /**
   * Like setup(), but GET /me/preferences returns a fresh Subject per call
   * (`prefsCalls[n]`) instead of a fixed value, so a test can resolve two
   * overlapping load() calls out of order.
   */
  function setupControlled() {
    const prefsCalls: Subject<UserPreferences>[] = [];
    updateCall = new Subject<UserPreferences>();
    updateCalls = [];
    toast = {
      info: vi.fn(),
      danger: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    };

    const me = {
      getPreferences: () => {
        const call = new Subject<UserPreferences>();
        prefsCalls.push(call);
        return call;
      },
      updatePreferences: (prefs: UserPreferences) => {
        updateCalls.push(prefs);
        return updateCall;
      },
    };
    const feedService = {
      getCategories: () => of(['News', 'Tech']),
      getLanguages: () => of(['en', 'zh-TW']),
    };

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [Preferences],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: { session: () => ({ user: { id: 'user-1' } }) } },
        { provide: MeService, useValue: me },
        { provide: FeedService, useValue: feedService },
        { provide: ToastService, useValue: toast },
      ],
    });

    const fixture = TestBed.createComponent(Preferences);
    fixture.detectChanges(); // triggers the constructor effect's initial load() -> prefsCalls[0]
    return { page: fixture.componentInstance, prefsCalls };
  }

  it('loads catalog options and the saved selection', () => {
    const page = setup({ preferred_categories: ['Tech'], preferred_languages: ['en'] });

    expect(page.categoryOptions()).toEqual(['News', 'Tech']);
    expect(page.languageOptions()).toEqual(['en', 'zh-TW']);
    expect(page.selectedCategories().has('Tech')).toBe(true);
    expect(page.selectedCategories().has('News')).toBe(false);
    expect(page.selectedLanguages().has('en')).toBe(true);
  });

  it('toggles a category selection on and off', () => {
    const page = setup();

    page.toggleCategory('News');
    expect(page.selectedCategories().has('News')).toBe(true);

    page.toggleCategory('News');
    expect(page.selectedCategories().has('News')).toBe(false);
  });

  it('does not mutate the previous selection Set in place', () => {
    const page = setup();
    const before = page.selectedCategories();

    page.toggleCategory('Tech');

    expect(before.has('Tech')).toBe(false);
    expect(page.selectedCategories().has('Tech')).toBe(true);
  });

  it('saves the current selection and shows a success toast', () => {
    const page = setup();
    page.toggleCategory('Tech');
    page.toggleLanguage('en');

    page.save();
    expect(page.saving()).toBe(true);

    updateCall.next({ preferred_categories: ['Tech'], preferred_languages: ['en'] });
    updateCall.complete();

    expect(page.saving()).toBe(false);
    expect(toast.success).toHaveBeenCalled();
  });

  it('sends the toggled selection as the payload, not the one that was loaded', () => {
    const page = setup({ preferred_categories: ['News'], preferred_languages: ['en'] });

    page.toggleCategory('News'); // deselect one that was already saved
    page.toggleCategory('Tech'); // and select one that was not
    page.toggleLanguage('zh-TW');

    page.save();

    // PUT /me/preferences replaces both lists wholesale, so a deselected
    // chip only actually goes away if it is absent from this payload.
    expect(updateCalls).toEqual([
      { preferred_categories: ['Tech'], preferred_languages: ['en', 'zh-TW'] },
    ]);
  });

  it('toggling chips writes nothing until 儲存', () => {
    const page = setup();

    page.toggleCategory('News');
    page.toggleCategory('News');
    page.toggleCategory('Tech');
    page.toggleLanguage('en');

    // A chip is local intent only — there is no per-toggle request to
    // debounce or de-duplicate, and no in-flight write for a rapid second
    // toggle to race against. The single PUT happens on 儲存偏好, whose
    // button is disabled for as long as `saving()` holds.
    expect(updateCalls).toEqual([]);
    expect(page.saving()).toBe(false);

    page.save();
    expect(updateCalls.length).toBe(1);
  });

  it('surfaces a toast and clears saving on failure', () => {
    const page = setup();

    page.save();
    updateCall.error(new Error('boom'));

    expect(page.saving()).toBe(false);
    expect(toast.danger).toHaveBeenCalled();
  });

  it('keeps the selection after a failed save, so retrying re-sends the same payload', () => {
    const page = setup({ preferred_categories: ['News'], preferred_languages: [] });
    page.toggleCategory('Tech');

    page.save();
    updateCall.error(new Error('boom'));

    // There is nothing to roll back to: the chips are the reader's own
    // unsaved intent, and clearing them on a failed PUT would mean picking
    // everything again before they could retry.
    expect([...page.selectedCategories()]).toEqual(['News', 'Tech']);

    page.save();

    expect(updateCalls).toEqual([
      { preferred_categories: ['News', 'Tech'], preferred_languages: [] },
      { preferred_categories: ['News', 'Tech'], preferred_languages: [] },
    ]);
  });

  it('keeps the form usable when only the catalog reads fail', () => {
    const page = setup(
      { preferred_categories: ['News'], preferred_languages: [] },
      {
        categories: throwError(() => new Error('boom')),
        languages: throwError(() => new Error('boom')),
      },
    );

    // Non-destructive, unlike a failed GET /me/preferences: the saved
    // selection did load, so hiding the whole form behind `error` would cost
    // more than the empty chip list and the two toasts do.
    expect(page.error()).toBe('');
    expect(page.loading()).toBe(false);
    expect(page.categoryOptions()).toEqual([]);
    expect(page.languageOptions()).toEqual([]);
    expect([...page.selectedCategories()]).toEqual(['News']);
    expect(toast.danger).toHaveBeenCalledTimes(2);
  });

  it('stays in the loading state until all three reads have settled', () => {
    const { page, prefsCalls } = setupControlled();

    // Both catalog reads already answered (they are synchronous `of(...)`);
    // GET /me/preferences has not. Clearing `loading` on a partial result
    // would flash the form with an empty selection before the saved one
    // lands — and that form's 儲存偏好 button PUTs whatever is on screen.
    expect(page.loading()).toBe(true);

    prefsCalls[0].next({ preferred_categories: ['Tech'], preferred_languages: [] });

    expect(page.loading()).toBe(false);
    expect(page.selectedCategories().has('Tech')).toBe(true);
  });

  it('ignores a stale preferences response once a newer load has started', () => {
    const { page, prefsCalls } = setupControlled();
    // prefsCalls[0] is the constructor effect's initial load(), still in flight.

    page.load(); // generation 2 — e.g. a manual retry, or an account switch.

    // The older request finally resolves after the newer one already started.
    prefsCalls[0].next({ preferred_categories: ['OLD'], preferred_languages: [] });
    expect(page.selectedCategories().has('OLD')).toBe(false);

    prefsCalls[1].next({ preferred_categories: ['NEW'], preferred_languages: [] });
    expect(page.selectedCategories().has('NEW')).toBe(true);
  });

  it('shows a retryable error instead of the form when preferences fail to load', () => {
    const { page, prefsCalls } = setupControlled();

    prefsCalls[0].error(new Error('boom'));

    // The form (and its save button) must stay hidden behind the error state —
    // otherwise a click on "儲存偏好" would PUT the two empty default Sets
    // over the reader's real saved preferences.
    expect(page.loading()).toBe(false);
    expect(page.error()).toBeTruthy();
    expect(page.selectedCategories().size).toBe(0);
    expect(page.selectedLanguages().size).toBe(0);
  });
});
