import type { Metadata } from "next";

import { Amount } from "@/components/amount";
import { ErrorBanner } from "@/components/banner";
import { ReportHeader } from "@/components/report-header";
import { loadReport } from "@/lib/reports/context";
import { fetchJournalBook } from "@/lib/reports/journal-book";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "仕訳帳 | ai-books viewer",
};

const EMPTY = "";

export default async function JournalPage({
  searchParams,
}: {
  searchParams: Promise<{ fy?: string | string[] }>;
}) {
  const { fy } = await searchParams;
  const result = await loadReport("journal", fy, (sql, year) =>
    fetchJournalBook(sql, {
      start: year.start_date,
      end: year.end_date,
      status: "posted",
    }),
  );
  if (!result.ok) return <ErrorBanner error={result.error} />;

  const { data: book, fiscalYear, fiscalYears } = result.data;

  return (
    <>
      <ReportHeader
        title="仕訳帳"
        subtitle="journal book（取引日 → 伝票番号 順）"
        period={`${fiscalYear.start_date} 〜 ${fiscalYear.end_date}`}
        basePath="/journal"
        fiscalYear={fiscalYear}
        fiscalYears={fiscalYears}
      />
      <div className="card scroll-x">
        <table className="report-table journal-table">
          <thead>
            <tr>
              <th scope="col">日付</th>
              <th scope="col">伝票番号</th>
              <th scope="col">勘定科目</th>
              <th scope="col" className="num">
                借方
              </th>
              <th scope="col" className="num">
                貸方
              </th>
              <th scope="col">摘要</th>
            </tr>
          </thead>
          <tbody>
            {book.entries.map((entry, entryIndex) => (
              <EntryRows
                key={entry.voucher_no ?? `entry-${entryIndex}`}
                entry={entry}
              />
            ))}
          </tbody>
          <tfoot>
            <tr className="subtotal">
              <th scope="row" colSpan={3}>
                合計
              </th>
              <td className="num">
                <Amount value={book.total_debit} />
              </td>
              <td className="num">
                <Amount value={book.total_credit} />
              </td>
              <td className="muted">借貸平均</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

function EntryRows({
  entry,
}: {
  entry: Awaited<ReturnType<typeof fetchJournalBook>>["entries"][number];
}) {
  return (
    <>
      {entry.lines.map((line, lineIndex) => (
        <tr
          key={`${entry.voucher_no ?? entry.entry_date}-${lineIndex}`}
          className={lineIndex === 0 ? "journal-entry-start" : undefined}
        >
          <th scope="row" className="nowrap">
            {lineIndex === 0 ? (
              entry.entry_date
            ) : (
              <span className="sr-only">{entry.entry_date}</span>
            )}
          </th>
          <td className="code">
            {lineIndex === 0 ? (entry.voucher_no ?? "—") : EMPTY}
          </td>
          <td>
            <span className="code">{line.account_code}</span>{" "}
            {line.account_name}
          </td>
          <td className="num">
            {line.side === "debit" ? <Amount value={line.amount} /> : EMPTY}
          </td>
          <td className="num">
            {line.side === "credit" ? <Amount value={line.amount} /> : EMPTY}
          </td>
          <td className="muted">
            {lineIndex === 0
              ? (entry.description ?? EMPTY)
              : (line.line_description ?? EMPTY)}
            {entry.status === "voided" && lineIndex === 0
              ? `（取消: ${entry.void_reason ?? ""}）`
              : EMPTY}
          </td>
        </tr>
      ))}
    </>
  );
}
