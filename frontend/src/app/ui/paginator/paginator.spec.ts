import { TestBed } from '@angular/core/testing';
import { ObPaginator } from './paginator';

describe('ObPaginator', () => {
  function make(total: number, page = 1, pageSize = 20) {
    const fixture = TestBed.createComponent(ObPaginator);
    Object.assign(fixture.componentInstance, { total, page, pageSize });
    fixture.detectChanges();
    return fixture;
  }

  it('rounds partial pages up', () => {
    expect(make(41).componentInstance.totalPages).toBe(3);
    expect(make(40).componentInstance.totalPages).toBe(2);
  });

  it('reports a single page when there is nothing to show, never zero', () => {
    // A zero here would render "1 / 0" and disable both directions.
    expect(make(0).componentInstance.totalPages).toBe(1);
  });

  it('clamps navigation to the available range', () => {
    const fixture = make(41, 1);
    const emitted: number[] = [];
    fixture.componentInstance.pageChange.subscribe((p) => emitted.push(p));

    fixture.componentInstance.go(0);
    fixture.componentInstance.go(99);

    // 0 clamps back to the current page 1 and is dropped as a no-op; 99 clamps to 3.
    expect(emitted).toEqual([3]);
  });

  it('does not emit when asked to go to the current page', () => {
    const fixture = make(41, 2);
    const emitted: number[] = [];
    fixture.componentInstance.pageChange.subscribe((p) => emitted.push(p));

    fixture.componentInstance.go(2);

    expect(emitted).toEqual([]);
  });

  it('resets to the first page when the page size changes', () => {
    // Page 3 of 20-per-page does not exist at 50 per page.
    const fixture = make(41, 3);
    const sizes: number[] = [];
    fixture.componentInstance.pageSizeChange.subscribe((s) => sizes.push(s));

    const select = (fixture.nativeElement as HTMLElement).querySelector('select')!;
    select.value = '50';
    select.dispatchEvent(new Event('change'));

    expect(sizes).toEqual([50]);
    expect(fixture.componentInstance.page).toBe(1);
  });

  it('offers only page sizes the backend accepts', () => {
    // GET /api/feeds caps page_size at 100.
    for (const option of make(100).componentInstance.pageSizeOptions) {
      expect(option).toBeLessThanOrEqual(100);
    }
  });

  it('disables the edges at the ends of the range', () => {
    const host = (fixture: ReturnType<typeof make>) => fixture.nativeElement as HTMLElement;

    const first = make(41, 1);
    expect(host(first).querySelector<HTMLButtonElement>('[aria-label="上一頁"]')!.disabled).toBe(
      true,
    );
    expect(host(first).querySelector<HTMLButtonElement>('[aria-label="下一頁"]')!.disabled).toBe(
      false,
    );

    const last = make(41, 3);
    expect(host(last).querySelector<HTMLButtonElement>('[aria-label="下一頁"]')!.disabled).toBe(
      true,
    );
  });
});
