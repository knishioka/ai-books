"use client";

/**
 * Print / save-as-PDF trigger. The only interactive control in the viewer — it calls the
 * browser's native print dialog, which the print stylesheet (`globals.css` `@media print`)
 * lays out for paper. It writes nothing; the viewer stays read-only.
 */
export function PrintButton() {
  return (
    <button
      type="button"
      className="print-button"
      onClick={() => window.print()}
    >
      印刷 / PDF
    </button>
  );
}
