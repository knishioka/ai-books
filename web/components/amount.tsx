import { formatAmount, isNegative } from "@/lib/format";

/**
 * A right-aligned monetary figure. Negative balances (赤字 / 控除) are tinted so a 当期純損失 or a
 * contra figure (期末商品棚卸高 等) reads at a glance. The underlying value is the report layer's
 * fixed-point string, so nothing is rounded for display.
 */
export function Amount({ value }: { value: string }) {
  return (
    <span className={isNegative(value) ? "amount amount-negative" : "amount"}>
      {formatAmount(value)}
    </span>
  );
}
