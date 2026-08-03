import { clampPage } from './paging';

describe('clampPage', () => {
  it('leaves a valid page alone', () => {
    expect(clampPage(2, 41, 20)).toBe(2);
    expect(clampPage(3, 41, 20)).toBe(3);
  });

  it('pulls an out-of-range page back to the last real one', () => {
    // 41 items shrank to 21: page 3 no longer exists.
    expect(clampPage(3, 21, 20)).toBe(2);
  });

  it('never returns 0, so an emptied queue still asks for a valid page', () => {
    expect(clampPage(5, 0, 20)).toBe(1);
    expect(clampPage(1, 0, 20)).toBe(1);
  });

  it('guards against a page below 1', () => {
    expect(clampPage(0, 40, 20)).toBe(1);
    expect(clampPage(-3, 40, 20)).toBe(1);
  });

  it('only ever moves a page down, which is what makes the retry terminate', () => {
    for (const [page, total, size] of [
      [3, 21, 20],
      [9, 5, 10],
      [2, 40, 20],
      [1, 1000, 25],
    ]) {
      expect(clampPage(page, total, size)).toBeLessThanOrEqual(Math.max(1, page));
    }
  });

  it('counts a partial final page', () => {
    expect(clampPage(3, 41, 20)).toBe(3);
    expect(clampPage(4, 41, 20)).toBe(3);
  });
});
