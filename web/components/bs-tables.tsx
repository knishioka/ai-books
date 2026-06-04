import type {
  BalanceSheetSectionSnapshot,
  BalanceSheetSnapshot,
} from "@/lib/reports/balance-sheet";
import { CATEGORY_LABELS } from "@/lib/reports/types";

import { Amount } from "./amount";

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

/** The 貸借対照表 tables (資産 / 負債 / 純資産), shared by `/bs` and the 決算書 (`/statements`) 4面. */
export function BalanceSheetTables({ bs }: { bs: BalanceSheetSnapshot }) {
  return (
    <>
      <div className="card">
        <table className="report-table">
          <caption className="section-caption">資産の部</caption>
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

      <div className="card">
        <table className="report-table">
          <caption className="section-caption">負債の部</caption>
          <Sections sections={bs.liabilities} />
          <tfoot>
            <tr className="subtotal">
              <td colSpan={2}>負債合計</td>
              <td className="num">
                <Amount value={bs.total_liabilities} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="card">
        <table className="report-table">
          <caption className="section-caption">純資産の部</caption>
          <Sections sections={bs.equity} />
          <tbody>
            <tr className="profit">
              <td colSpan={2}>当期純利益</td>
              <td className="num">
                <Amount value={bs.net_income} />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr className="grand-total">
              <td colSpan={2}>純資産合計</td>
              <td className="num">
                <Amount value={bs.total_equity} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p className="report-note">
        貸借一致：資産合計 {bs.total_assets} ＝ 負債合計 {bs.total_liabilities}{" "}
        ＋ 純資産合計 {bs.total_equity}
      </p>
    </>
  );
}
