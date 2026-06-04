/**
 * Tile a fiscal year into accounting months, mirroring `ai_books.aggregation.month_windows`.
 *
 * The 月次推移 (#18) and 月別売上・仕入 (#23) reports must show *every* month the year touches —
 * including quiet months with no activity — so the series stays tiled across the whole fiscal
 * year. The SQL sums are keyed by `to_char(date_trunc('month', …), 'YYYY-MM')`; this produces
 * the same ordered `YYYY-MM` labels to join them against, counting months (not assuming the
 * year starts in January) so an April→March year tiles cleanly too.
 */

/** Ordered `YYYY-MM` labels for every calendar month `[start, end]` (ISO dates) touches. */
export function monthLabels(start: string, end: string): string[] {
  let year = Number(start.slice(0, 4));
  let month = Number(start.slice(5, 7));
  const endYear = Number(end.slice(0, 4));
  const endMonth = Number(end.slice(5, 7));
  const labels: string[] = [];
  while (year < endYear || (year === endYear && month <= endMonth)) {
    labels.push(
      `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`,
    );
    if (month === 12) {
      year += 1;
      month = 1;
    } else {
      month += 1;
    }
  }
  return labels;
}
