import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ImpactValue, NodeResult, Result } from "axe-core";

/**
 * Allowlist for the axe-core accessibility regression sweep (issue #166).
 *
 * The sweep gates on **serious / critical** violations being zero across every screen. Those are
 * fixed in place, never silenced — so this allowlist exists only for the rare genuinely
 * unavoidable case (e.g. a third-party widget we cannot patch). It is keyed by `ruleId × selector`
 * so a single tolerated node never widens the gate for a whole rule, and **every entry must carry
 * a non-empty `reason`**: a silent allow is forbidden. The on-disk file (`a11y-allowlist.json`)
 * starts empty by design.
 *
 * `parseAllowlist` is intentionally a pure function (no I/O) so the reason-required invariant can
 * be unit-tested with fixtures; `loadAllowlist` is the thin file-reading wrapper the spec uses.
 */
export type A11yAllowEntry = {
  /** axe rule ID to tolerate, e.g. `"color-contrast"`. */
  ruleId: string;
  /** The node's CSS selector as axe reports it in `node.target` (joined for nested frames). */
  selector: string;
  /** Why this violation is unavoidable. Required and non-empty — see module docstring. */
  reason: string;
};

const ALLOWLIST_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "a11y-allowlist.json",
);

/** Impacts that fail the gate; anything below (`moderate` / `minor`) is report-only (#166). */
export const GATING_IMPACTS: ReadonlySet<ImpactValue> = new Set<ImpactValue>([
  "serious",
  "critical",
]);

/**
 * Validate raw allowlist entries, enforcing that **every entry has a non-empty `ruleId`,
 * `selector` and `reason`**. Throws on the first malformed entry rather than dropping it — a
 * broken allowlist must fail loudly, never silently widen the gate. Extra keys are ignored so the
 * JSON file can carry documentation fields.
 */
export function parseAllowlist(raw: unknown): A11yAllowEntry[] {
  if (!Array.isArray(raw)) {
    throw new Error("a11y allowlist `entries` must be a JSON array");
  }
  return raw.map((entry, i) => {
    if (typeof entry !== "object" || entry === null) {
      throw new Error(`a11y allowlist entry #${i} must be an object`);
    }
    const record = entry as Record<string, unknown>;
    const fields: Array<["ruleId" | "selector" | "reason", unknown]> = [
      ["ruleId", record.ruleId],
      ["selector", record.selector],
      ["reason", record.reason],
    ];
    for (const [key, value] of fields) {
      if (typeof value !== "string" || value.trim() === "") {
        throw new Error(
          `a11y allowlist entry #${i} requires a non-empty "${key}"`,
        );
      }
    }
    return {
      ruleId: record.ruleId as string,
      selector: record.selector as string,
      reason: record.reason as string,
    };
  });
}

/** Read and validate `a11y-allowlist.json`. The file is `{ "entries": [...] }`. */
export function loadAllowlist(): A11yAllowEntry[] {
  const root = JSON.parse(readFileSync(ALLOWLIST_PATH, "utf8")) as {
    entries?: unknown;
  };
  return parseAllowlist(root.entries ?? []);
}

/** Serialize an axe `node.target` (possibly nested for frames/shadow DOM) to a stable string. */
function serializeTarget(target: NodeResult["target"]): string {
  return target.flat(Infinity).join(" ");
}

/** True when this violation node is covered by an allowlist entry for the same rule. */
function isNodeAllowed(
  ruleId: string,
  node: NodeResult,
  allow: A11yAllowEntry[],
): boolean {
  const serialized = serializeTarget(node.target);
  const parts = node.target.flat(Infinity).map(String);
  return allow.some(
    (e) =>
      e.ruleId === ruleId &&
      (e.selector === serialized || parts.includes(e.selector)),
  );
}

/**
 * Subtract allowlisted nodes from each violation, keeping only violations that still have at least
 * one un-allowlisted node. The spec then filters the result by {@link GATING_IMPACTS} to compute
 * the set that fails the gate.
 */
export function filterAllowed(
  violations: Result[],
  allow: A11yAllowEntry[],
): Result[] {
  return violations
    .map((v) => ({
      ...v,
      nodes: v.nodes.filter((n) => !isNodeAllowed(v.id, n, allow)),
    }))
    .filter((v) => v.nodes.length > 0);
}
