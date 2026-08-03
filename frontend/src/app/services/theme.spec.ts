import { TestBed } from '@angular/core/testing';
import { ThemeService } from './theme';

const STORAGE_KEY = 'driftread_theme';

describe('ThemeService', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    TestBed.resetTestingModule();
  });

  it('defaults to system, which means no attribute so the media query decides', () => {
    const theme = TestBed.inject(ThemeService);
    expect(theme.mode()).toBe('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('writes an explicit choice to the root element and to storage', () => {
    const theme = TestBed.inject(ThemeService);
    theme.set('light');

    expect(theme.mode()).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
  });

  it('restores a stored choice on construction', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    const theme = TestBed.inject(ThemeService);

    expect(theme.mode()).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('going back to system removes the attribute rather than setting a value', () => {
    const theme = TestBed.inject(ThemeService);
    theme.set('dark');
    theme.set('system');

    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('ignores an unrecognised stored value instead of applying it', () => {
    localStorage.setItem(STORAGE_KEY, 'solarized');
    const theme = TestBed.inject(ThemeService);

    expect(theme.mode()).toBe('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});
