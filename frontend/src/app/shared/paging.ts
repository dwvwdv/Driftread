/**
 * Page index to use after rows have left the current page.
 *
 * Every admin queue has the same shape: rows are removed (approved, rejected,
 * archived, blocked), the total shrinks, and the page the operator is sitting on
 * can end up past the end. The backend answers an out-of-range page with an empty
 * list rather than an error, and every one of these screens hides its paginator in
 * the empty branch — so an uncorrected index strands the operator on a blank page
 * with no control left to navigate back.
 *
 * Clamped to at least 1 so an empty queue still asks for a valid page.
 */
export function clampPage(page: number, total: number, pageSize: number): number {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  return Math.min(Math.max(1, page), lastPage);
}
