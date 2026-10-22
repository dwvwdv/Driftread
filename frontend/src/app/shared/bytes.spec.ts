import { formatMiB, formatSignedMiB } from './bytes';

describe('formatMiB', () => {
  it('formats sizes and unknowns consistently', () => {
    expect(formatMiB(200 * 1024 * 1024)).toBe('200.0 MiB');
    expect(formatMiB(-2 * 1024 * 1024)).toBe('-2.0 MiB');
    expect(formatMiB(0)).toBe('0.0 MiB');
    expect(formatMiB(null)).toBe('—');
  });

  it('marks growth with an explicit sign', () => {
    expect(formatSignedMiB(1024 * 1024)).toBe('+1.0 MiB');
    expect(formatSignedMiB(-1024 * 1024)).toBe('-1.0 MiB');
    expect(formatSignedMiB(0)).toBe('0.0 MiB');
    expect(formatSignedMiB(null)).toBe('—');
  });
});
