/**
 * Query-string filters shared by report pages.
 *
 * Account codes are stored as text, but viewer filter links/selects use compact accounting
 * codes. Bound the accepted query value before using it in cache keys so public URLs cannot
 * create unbounded cache-key cardinality.
 */

const ACCOUNT_CODE_PARAM = /^[0-9A-Za-z_-]{1,32}$/;
const FISCAL_YEAR_PARAM = /^FY[0-9]{4}$/;

export type QueryParamValue = string | string[] | null | undefined;

function firstQueryParamValue(value: QueryParamValue): string | null {
  const first = Array.isArray(value) ? value[0] : value;
  return first && first !== "" ? first : null;
}

export function normalizeAccountCodeParam(
  value: QueryParamValue,
): string | null {
  const normalized = firstQueryParamValue(value);
  return normalized && ACCOUNT_CODE_PARAM.test(normalized) ? normalized : null;
}

export function normalizeFiscalYearParam(
  value: QueryParamValue,
): string | null {
  const normalized = firstQueryParamValue(value);
  return normalized && FISCAL_YEAR_PARAM.test(normalized) ? normalized : null;
}
