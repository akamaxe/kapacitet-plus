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
