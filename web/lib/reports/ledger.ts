/**
 * The one signing rule the whole codebase derives balances from — the TS twin of
 * `ai_books.ledger`. Kept tiny and pure so the trial balance, 月次推移, 総勘定元帳, P/L, B/S and
 * 精算表 can never disagree about which side an account's normal balance sits on (the same
 * guarantee the Python `balance_from_totals` gives the report layer).
 */

import type { Money } from "../money";
import type { NormalSide } from "./types";

/**
 * Sign a pair of 借方 / 貸方 totals into the account's normal direction.
 *
 * Debit-normal (資産/費用) → `debit − credit`; credit-normal (負債/純資産/収益) → `credit − debit`.
 * A negative result means the account sits opposite its normal balance.
 */
export function balanceFromTotals(
  debitTotal: Money,
  creditTotal: Money,
  normal: NormalSide,
): Money {
  return normal === "debit"
    ? debitTotal - creditTotal
    : creditTotal - debitTotal;
}

/**
 * Sign one line's `amount` by its effect on a `normal`-balance account: positive when `side`
 * matches the normal side (the line increases the balance), negative otherwise. Mirrors
 * `ai_books.ledger.signed_delta`.
 */
export function signedDelta(
  side: NormalSide,
  normal: NormalSide,
  amount: Money,
): Money {
  return side === normal ? amount : -amount;
}
