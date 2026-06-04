/**
 * Display formatting for the viewer (client- and server-safe — no DB import).
 *
 * The report layer hands amounts as fixed-point strings ("300000.00", "-350000.00"). For the
 * screen we group the integer part with thousands separators and a ¥ sign, dropping a trailing
 * ".00" (the common whole-yen case) while preserving any 端数. The raw string stays the source of
 * truth — this only affects presentation.
 */

/** Format a fixed-point amount string as a grouped ¥ figure ("¥300,000", "-¥350,000"). */
export function formatAmount(value: string): string {
  const negative = value.startsWith("-");
  const body = negative ? value.slice(1) : value;
  const [intPart = "0", fracPart = "00"] = body.split(".");
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = fracPart === "00" || fracPart === "" ? "" : `.${fracPart}`;
  return `${negative ? "-" : ""}¥${grouped}${frac}`;
}

/** True when an amount string is negative (for styling 赤字 / マイナス figures). */
export function isNegative(value: string): boolean {
  return value.startsWith("-");
}

/** Side label: 借方 / 貸方. */
export function sideLabel(side: "debit" | "credit"): string {
  return side === "debit" ? "借方" : "貸方";
}
