import { TestBed } from '@angular/core/testing';
import { ObTabs } from './tabs';

/**
 * These cover the behaviour Material used to provide and that this component now
 * owns: ARIA wiring and the roving-tabindex keyboard contract.
 */
describe('ObTabs', () => {
  function make(selected = 0) {
    const fixture = TestBed.createComponent(ObTabs);
    fixture.componentInstance.tabs = ['收藏', '稍後閱讀', '封存'];
    fixture.componentInstance.selected = selected;
    fixture.detectChanges();
    return fixture;
  }

  function tabs(fixture: ReturnType<typeof make>): HTMLButtonElement[] {
    return Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
  }

  function press(fixture: ReturnType<typeof make>, key: string) {
    const list = (fixture.nativeElement as HTMLElement).querySelector('[role="tablist"]')!;
    list.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    fixture.detectChanges();
  }

  it('marks exactly one tab selected and keeps the rest out of the tab order', () => {
    const fixture = make(1);
    const list = tabs(fixture);

    expect(list.map((t) => t.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false']);
    expect(list.map((t) => t.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);
  });

  it('points each tab at the panel, and the panel back at the active tab', () => {
    const fixture = make(1);
    const host = fixture.nativeElement as HTMLElement;
    const panel = host.querySelector('[role="tabpanel"]')!;

    expect(tabs(fixture)[0].getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.getAttribute('aria-labelledby')).toBe(tabs(fixture)[1].id);
  });

  it('moves selection with the arrow keys and wraps at both ends', () => {
    const fixture = make(0);
    const emitted: number[] = [];
    fixture.componentInstance.selectedChange.subscribe((i) => emitted.push(i));

    press(fixture, 'ArrowRight');
    press(fixture, 'ArrowRight');
    press(fixture, 'ArrowRight'); // wraps 2 -> 0
    press(fixture, 'ArrowLeft'); // wraps 0 -> 2

    expect(emitted).toEqual([1, 2, 0, 2]);
  });

  it('supports Home and End', () => {
    const fixture = make(1);
    const emitted: number[] = [];
    fixture.componentInstance.selectedChange.subscribe((i) => emitted.push(i));

    press(fixture, 'End');
    press(fixture, 'Home');

    expect(emitted).toEqual([2, 0]);
  });

  it('moves focus along with selection, so the group stays one tab stop', () => {
    const fixture = make(0);
    press(fixture, 'ArrowRight');

    expect(document.activeElement).toBe(tabs(fixture)[1]);
  });

  it('ignores keys it does not handle', () => {
    const fixture = make(0);
    const emitted: number[] = [];
    fixture.componentInstance.selectedChange.subscribe((i) => emitted.push(i));

    press(fixture, 'a');
    press(fixture, 'Enter');

    expect(emitted).toEqual([]);
  });

  it('does not re-emit when the active tab is clicked again', () => {
    const fixture = make(1);
    const emitted: number[] = [];
    fixture.componentInstance.selectedChange.subscribe((i) => emitted.push(i));

    tabs(fixture)[1].click();

    expect(emitted).toEqual([]);
  });
});
