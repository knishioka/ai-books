/**
 * Exact fixed-point money, mirroring the Python report layer's `Decimal` + `numeric(18, 2)`.
 *
 * Every amount in the books is `numeric(18, 2)` (浮動小数禁止). JavaScript's `number` is a
 * float and would silently lose precision on a sum of yen, so a balance must never become a
 * float on the way through the viewer. We carry money as an integer count of 銭 (1/100 円) in
 * a `bigint` — addition, subtraction and comparison are the only operations the reports need,
 * all exact — and serialize with {@link formatMoney}, which reproduces Python
 * `str(Decimal(x).quantize(Decimal("0.01")))` byte-for-byte ("300000.00", "-350000.00",
 * "0.00", never "-0.00" or "3E+5"). This is what lets the viewer's figures match the
 * golden snapshots (#17) exactly.
 */

/** A money amount as an integer number of 銭 (sen, 1/100 円). */
export type Money = bigint;

/** Zero yen. */
export const ZERO: Money = 0n;

/**
 * Parse a Postgres `numeric`/`Decimal` string (or integer) into 銭.
 *
 * Accepts the shapes the `postgres` driver returns for `numeric` ("420000.00", "0") and the
 * whole-yen integers the e-Tax layer carries ("1650000"). The fractional part is padded /
 * truncated to exactly two digits, matching `numeric(18, 2)`.
 */
export function parseMoney(value: string | number | bigint): Money {
  if (typeof value === "bigint") return value * 100n;
  const text = String(value).trim();
  const negative = text.startsWith("-");
  const body = negative ? text.slice(1) : text;
  const [intPart = "0", fracPart = ""] = body.split(".");
  const frac = `${fracPart}00`.slice(0, 2);
  const sen = BigInt(intPart || "0") * 100n + BigInt(frac || "0");
  return negative ? -sen : sen;
}

/**
 * Parse a `numeric` value that is already known to be whole yen (no 端数) into 銭.
 * (Kept distinct from {@link parseMoney} only for call-site readability.)
 */
export const parseAmount = parseMoney;

/** Serialize 銭 as a fixed 2-dp string — the `numeric(18, 2)` / golden shape. */
export function formatMoney(sen: Money): string {
  const negative = sen < 0n;
  const abs = negative ? -sen : sen;
  const yen = abs / 100n;
  const frac = (abs % 100n).toString().padStart(2, "0");
  return `${negative ? "-" : ""}${yen.toString()}.${frac}`;
}

/** Sum a list of 銭 amounts (exact). */
export function sumMoney(values: Iterable<Money>): Money {
  let total = ZERO;
  for (const value of values) total += value;
  return total;
}
