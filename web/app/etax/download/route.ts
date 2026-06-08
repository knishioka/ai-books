import "server-only";

import {
  EtaxValidationError,
  parseEtaxFormat,
  renderEtax,
} from "@/lib/etax/export";
import { buildSampleEtaxExport } from "@/lib/etax/sample";
import { getSql, NO_DB_ERROR } from "@/lib/db";
import { resolveFiscalYear } from "@/lib/reports/fiscal-year";

export const dynamic = "force-dynamic";

/**
 * Stream the e-Tax 取込データ (CSV / XML) as a download for the selected 会計年度.
 *
 * Read-only: it builds the 決算書 from the database, maps it to the versioned e-Tax 様式 and
 * renders the file in memory — nothing is written. The generated file carries the 事業者's
 * 確定数値 (秘密情報) so it is served with `Cache-Control: no-store` and never persisted.
 */
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);

  let format;
  try {
    format = parseEtaxFormat(url.searchParams.get("format") ?? "csv");
  } catch (err) {
    return new Response(err instanceof Error ? err.message : "invalid format", {
      status: 400,
    });
  }

  const sql = getSql();
  if (!sql) {
    return new Response(NO_DB_ERROR, { status: 503 });
  }

  try {
    const fiscalYear = await resolveFiscalYear(
      sql,
      url.searchParams.get("fy") ?? undefined,
    );
    if (!fiscalYear) {
      return new Response("会計年度が登録されていません。", { status: 404 });
    }
    const body = renderEtax(await buildSampleEtaxExport(sql, fiscalYear), format);
    const contentType =
      format === "csv"
        ? "text/csv; charset=utf-8"
        : "application/xml; charset=utf-8";
    const filename = `etax_${fiscalYear.name}.${format}`;
    return new Response(body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": `attachment; filename="${filename}"`,
        "Cache-Control": "no-store",
      },
    });
  } catch (err) {
    if (err instanceof EtaxValidationError) {
      return new Response(err.message, { status: 422 });
    }
    return new Response(err instanceof Error ? err.message : "Unknown error.", {
      status: 500,
    });
  }
}
