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

/**
 * A 段階利益 row. `step` (1〜4) drives the staircase indent so 売上総利益 → 営業利益 → 経常利益 →
 * 当期純利益 cascade rightward, each 罫 leading the eye to the next; the last step (当期純利益) is
 * the emphasized 結び (`final`).
 */
function Profit({
  label,
  amount,
  step,
  final = false,
}: {
  label: string;
  amount: string;
  step: number;
  final?: boolean;
}) {
  return (
    <tr
      className={
        final ? "profit profit-step profit-final" : "profit profit-step"
      }
      data-step={step}
    >
      <td colSpan={2}>
        <span className="profit-label">{label}</span>
      </td>
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
        <Profit label="売上総利益" amount={pl.gross_profit} step={1} />
        <Section section={pl.selling_admin_expenses} />
        <Profit label="営業利益" amount={pl.operating_income} step={2} />
        <Section section={pl.non_operating_income} />
        <Section section={pl.non_operating_expenses} />
        <Profit label="経常利益" amount={pl.ordinary_income} step={3} />
        <Profit
          label="当期純利益（青色申告特別控除前）"
          amount={pl.net_income}
          step={4}
          final
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
