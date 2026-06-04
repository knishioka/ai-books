import type { FiscalYear } from "@/lib/reports/fiscal-year";

/**
 * Fiscal-year picker — a native GET `<form>` (no client JS): selecting a year and submitting
 * navigates to `basePath?fy=…`, so the period switch works on a read-only, server-rendered page.
 * `extra` carries any other query params (e.g. the ledger's selected account) through the switch.
 */
export function PeriodSelector({
  basePath,
  current,
  years,
  extra,
}: {
  basePath: string;
  current: string;
  years: FiscalYear[];
  extra?: Record<string, string>;
}) {
  return (
    <form method="get" action={basePath} className="period-selector">
      {extra &&
        Object.entries(extra).map(([key, value]) => (
          <input key={key} type="hidden" name={key} value={value} />
        ))}
      <label>
        会計年度
        <select name="fy" defaultValue={current}>
          {years.map((year) => (
            <option key={year.name} value={year.name}>
              {year.name}（{year.start_date} 〜 {year.end_date}）
            </option>
          ))}
        </select>
      </label>
      <button type="submit">表示</button>
    </form>
  );
}
