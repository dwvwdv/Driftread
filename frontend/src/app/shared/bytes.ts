/**
 * Byte counts on the admin storage panels, in one unit and precision so the
 * dashboard and the per-table panel on the same page always agree.
 * `null` (unknown, e.g. a stats reset) renders as an em dash.
 */
export function formatMiB(value: number | null): string {
  if (value === null) return '—';
  const sign = value < 0 ? '-' : '';
  return `${sign}${(Math.abs(value) / 1024 / 1024).toFixed(1)} MiB`;
}

/** Growth deltas: an explicit `+` for positive values. */
export function formatSignedMiB(value: number | null): string {
  return value !== null && value > 0 ? `+${formatMiB(value)}` : formatMiB(value);
}
