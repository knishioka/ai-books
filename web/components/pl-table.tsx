import type {
  ProfitAndLossSectionSnapshot,
  ProfitAndLossSnapshot,
} from "@/lib/reports/profit-and-loss";

import { Amount } from "./amount";

function Section({ section }: { section: ProfitAndLossSectionSnapshot }) {
  return (
    <>
      <tr className="section-head">
        <td colSpan={2}>【{section.label}】</td>
        <td className="num" />
      </tr>
      {section.lines.map((line) => (
        <tr key={line.code}>
          <td className="code">{line.code}</td>
          <td>{line.name}</td>
          <td className="num">
            <Amount value={line.amount} />
          </td>
        </tr>
      ))}
      <tr className="subtotal">
        <td colSpan={2}>{section.label} 計</td>
        <td className="num">
          <Amount value={section.subtotal} />
        </td>
      </tr>
    </>
  );
}

function Profit({ label, amount }: { label: string; amount: string }) {
  return (
    <tr className="profit">
      <td colSpan={2}>{label}</td>
      <td className="num">
        <Amount value={amount} />
      </td>
    </tr>
  );
}

/** The staged 損益計算書 table, shared by `/pl` and the 決算書 (`/statements`) 1面. */
export function ProfitAndLossTable({ pl }: { pl: ProfitAndLossSnapshot }) {
  return (
    <table className="report-table">
      <thead>
        <tr>
          <th>コード</th>
          <th>科目名</th>
          <th className="num">金額</th>
        </tr>
      </thead>
      <tbody>
        <Section section={pl.sales} />
        <Section section={pl.cost_of_goods_sold} />
        <Profit label="売上総利益" amount={pl.gross_profit} />
        <Section section={pl.selling_admin_expenses} />
        <Profit label="営業利益" amount={pl.operating_income} />
        <Section section={pl.non_operating_income} />
        <Section section={pl.non_operating_expenses} />
        <Profit label="経常利益" amount={pl.ordinary_income} />
        <Profit
          label="当期純利益（青色申告特別控除前）"
          amount={pl.net_income}
        />
        {pl.unclassified.length > 0 && (
          <>
            <tr className="section-head warn-row">
              <td colSpan={2}>【未分類科目（表示区分なし）】</td>
              <td className="num" />
            </tr>
            {pl.unclassified.map((line) => (
              <tr key={line.code}>
                <td className="code">{line.code}</td>
                <td>{line.name}</td>
                <td className="num">
                  <Amount value={line.amount} />
                </td>
              </tr>
            ))}
          </>
        )}
      </tbody>
    </table>
  );
}
