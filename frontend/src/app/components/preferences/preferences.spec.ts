import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { Preferences } from './preferences';
import { AuthService } from '../../services/auth';
import { MeService } from '../../services/me';
import { FeedService } from '../../services/feed';
import { ToastService } from '../../ui/toast/toast';
import { UserPreferences } from '../../models';

describe('Preferences', () => {
  let updateCall: Subject<UserPreferences>;
  let toast: {
    info: ReturnType<typeof vi.fn>;
    danger: ReturnType<typeof vi.fn>;
    success: ReturnType<typeof vi.fn>;
    warning: ReturnType<typeof vi.fn>;
  };

  function setup(saved: UserPreferences = { preferred_categories: [], preferred_languages: [] }) {
    updateCall = new Subject<UserPreferences>();
    toast = {
      info: vi.fn(),
      danger: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    };

    const me = {
      getPreferences: () => of(saved),
      updatePreferences: () => updateCall,
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
      updatePreferences: () => updateCall,
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

  it('surfaces a toast and clears saving on failure', () => {
    const page = setup();

    page.save();
    updateCall.error(new Error('boom'));

    expect(page.saving()).toBe(false);
    expect(toast.danger).toHaveBeenCalled();
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
