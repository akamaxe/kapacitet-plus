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

/** One single-phase device tried on each phase (engine/request.py `_phase_check`), in one sentence. */
export function phaseText(t: T, f: F, d: any, limit: number): string {
  const rows: any[] = d.by_phase ?? [];
  const best = rows.find((r) => r.phase === d.best) ?? rows[0];
  const worst = rows.find((r) => r.phase === d.worst) ?? rows[0];
  if (!best || !worst) return "";
  const vmin = Math.min(...rows.map((r) => r.v_min_pu));
  const vmax = Math.max(...rows.map((r) => r.v_max_pu));
  return t("req.phase.detail", {
    device: t(`req.phase.dev.${d.device}`, { kw: f(d.kw, 1) }),
    best: f(best.vuf_pct, 2), bestPh: best.phase, worst: f(worst.vuf_pct, 2), worstPh: worst.phase,
    limit: f(limit, 0), vmin: f(vmin, 3), vmax: f(vmax, 3),
  });
}

/** The phase verdict's label: any phase, one named phase, or three-phase needed. */
export function phaseVerdict(t: T, d: any): { kind: "good" | "warning" | "critical"; text: string } {
  if (d.ok_all) return { kind: "good", text: t("req.phase.any") };
  if (d.ok_best) return { kind: "warning", text: t("req.phase.use", { ph: d.best }) };
  return { kind: "critical", text: t("req.phase.none") };
}

/** A house's minimum fault current against its main fuse (results/protection.json). */
export function faultText(t: T, f: F, h: any, seconds: number): string {
  return h.ok
    ? t("req.faultOk", { a: f(h.ik1_min_a), fuse: f(h.main_fuse_a), s: f(seconds) })
    : t("req.faultShort", { a: f(h.ik1_min_a), gate: f(h.gate_5s_a), fuse: f(h.main_fuse_a), s: f(seconds) });
}

/** The main fuse's verdict in one sentence (engine/request.py `fuse_verdict`). */
export function fuseText(t: T, f: F, fuse: any): string {
  const a = f(fuse.phase_current_a, 1);
  if (fuse.ok) return t("req.fuse.okDetail", { fuse: f(fuse.fuse_a), a });
  if (fuse.fuse_needed_a != null) return t("req.fuse.needDetail", { fuse: f(fuse.fuse_a), a, need: f(fuse.fuse_needed_a) });
  return t("req.fuse.beyond", { a, max: f(fuse.largest_a) });
}
