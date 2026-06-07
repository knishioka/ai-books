import { formatMoney, parseMoney } from "@/lib/money";
import type {
  BalanceSheetSectionSnapshot,
  BalanceSheetSnapshot,
} from "@/lib/reports/balance-sheet";
import { CATEGORY_LABELS } from "@/lib/reports/types";

import { Amount } from "./amount";
import { formatAmount } from "@/lib/format";

function Sections({ sections }: { sections: BalanceSheetSectionSnapshot[] }) {
  return (
    <>
      {sections.map((section) => (
        <tbody key={section.category} className="bs-section">
          <tr className="section-head">
            <td colSpan={2}>
              {CATEGORY_LABELS[section.category] ?? section.category}
            </td>
            <td className="num" />
          </tr>
          {section.lines.map((line) => (
            <tr key={line.code}>
              <td className="code">{line.code}</td>
              <td>{line.name}</td>
              <td className="num">
                <Amount value={line.balance} />
              </td>
            </tr>
          ))}
          <tr className="subtotal">
            <td colSpan={2}>
              {CATEGORY_LABELS[section.category] ?? section.category} 計
            </td>
            <td className="num">
              <Amount value={section.subtotal} />
            </td>
          </tr>
        </tbody>
      ))}
    </>
  );
}

function PartHead({ label }: { label: string }) {
  return (
    <tbody className="bs-part-head">
      <tr className="part-head">
        <td colSpan={3}>{label}</td>
      </tr>
    </tbody>
  );
}

/**
 * The 貸借対照表, laid out as a 借方/貸方 見開き (T字): 資産の部 (左) ↔ 負債・純資産の部 (右),
 * with each side footing to the same figure and a 貸借一致 line drawing the two together. Shared
 * by `/bs` and the 決算書 (`/statements`) 4面.
 */
export function BalanceSheetTables({ bs }: { bs: BalanceSheetSnapshot }) {
  // 貸方側の脚 = 負債合計 + 純資産合計（純資産合計に 当期純利益 は織り込み済み）。貸借一致が
  // 成り立つので数値は資産合計と一致するが、意味どおり足し上げて表示する。
  const liabilitiesEquityTotal = formatMoney(
    parseMoney(bs.total_liabilities) + parseMoney(bs.total_equity),
  );

  return (
    <>
      <div className="bs-tform">
        <div className="card bs-debit">
          <table className="report-table">
            <caption className="section-caption">
              資産の部 <span className="side-tag">借方</span>
            </caption>
            <Sections sections={bs.assets} />
            <tfoot>
              <tr className="grand-total">
                <td colSpan={2}>資産合計</td>
                <td className="num">
                  <Amount value={bs.total_assets} />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        <div className="card bs-credit">
          <table className="report-table">
            <caption className="section-caption">
              負債・純資産の部 <span className="side-tag">貸方</span>
            </caption>
            <PartHead label="負債の部" />
            <Sections sections={bs.liabilities} />
            <tbody>
              <tr className="subtotal part-total">
                <td colSpan={2}>負債合計</td>
                <td className="num">
                  <Amount value={bs.total_liabilities} />
                </td>
              </tr>
            </tbody>
            <PartHead label="純資産の部" />
            <Sections sections={bs.equity} />
            <tbody>
              <tr className="profit">
                <td colSpan={2}>当期純利益</td>
                <td className="num">
                  <Amount value={bs.net_income} />
                </td>
              </tr>
              <tr className="subtotal part-total">
                <td colSpan={2}>純資産合計</td>
                <td className="num">
                  <Amount value={bs.total_equity} />
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr className="grand-total">
                <td colSpan={2}>負債・純資産合計</td>
                <td className="num">
                  <Amount value={liabilitiesEquityTotal} />
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      <p className="balance-check">
        <span className="balance-check-seal">貸借一致</span>
        <span className="balance-check-body">
          資産合計 {formatAmount(bs.total_assets)} ＝ 負債・純資産合計{" "}
          {formatAmount(liabilitiesEquityTotal)}
        </span>
      </p>
    </>
  );
}
