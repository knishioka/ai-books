/**
 * Query-string filters shared by report pages.
 *
 * Account codes are stored as text, but viewer filter links/selects use compact accounting
 * codes. Bound the accepted query value before using it in cache keys so public URLs cannot
 * create unbounded cache-key cardinality.
 */

const ACCOUNT_CODE_PARAM = /^[0-9A-Za-z_-]{1,32}$/;

export function normalizeAccountCodeParam(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  return ACCOUNT_CODE_PARAM.test(value) ? value : null;
}
