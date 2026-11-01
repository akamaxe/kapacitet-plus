/**
 * Why the static study approved or rejected a request, in the current language.
 *
 * The engine returns a code and a value next to its English sentence (engine/request.py
 * `_reason`), so the interface can say it in either language; an answer without a code falls
 * back to the English text.
 */
export function reasonText(
  t: (k: string, v?: Record<string, string | number>) => string,
  f: (x: number | null | undefined, d?: number) => string,
  s: { reason?: string; reason_code?: string; reason_value?: number | null } | undefined,
): string {
  if (!s?.reason_code) return s?.reason ?? "";
  const pu = s.reason_code === "over_v" || s.reason_code === "under_v";
  return t(`req.reason.${s.reason_code}`, { v: s.reason_value == null ? "" : f(s.reason_value, pu ? 3 : 0) });
}

type T = (k: string, v?: Record<string, string | number>) => string;
type F = (x: number | null | undefined, d?: number) => string;

/** The static study's own numbers at its worst-case hour: transformer, voltage, busiest cable. */
export function staticNumbers(t: T, f: F, s: any): string {
  if (!s || s.trafo_loading_pct === undefined) return "";
  return s.max_voltage_pu !== undefined
    ? t("req.staticNumbers", { trafo: f(s.trafo_loading_pct), v: f(s.max_voltage_pu, 3), line: f(s.max_line_loading_pct) })
    : t("req.staticImportNumbers", { trafo: f(s.trafo_loading_pct), v: f(s.min_voltage_pu, 3), line: f(s.max_line_loading_pct) });
}

/** The main fuse's verdict in one sentence (engine/request.py `fuse_verdict`). */
export function fuseText(t: T, f: F, fuse: any): string {
  const a = f(fuse.phase_current_a, 1);
  if (fuse.ok) return t("req.fuse.okDetail", { fuse: f(fuse.fuse_a), a });
  if (fuse.fuse_needed_a != null) return t("req.fuse.needDetail", { fuse: f(fuse.fuse_a), a, need: f(fuse.fuse_needed_a) });
  return t("req.fuse.beyond", { a, max: f(fuse.largest_a) });
}
