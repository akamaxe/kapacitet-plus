/**
 * Which spot prices a price-derived figure rests on (api: the `prices` block of /business,
 * /reinforcement, /overinstallation and an owner case; engine/summaries.price_basis).
 *
 * The central figure is the mean of the current market regime's years; its range runs over every
 * weather year with prices. Each source is attributed for the years it gave: elprisetjustnu in its
 * own words, ENTSO-E (at the ECB's rate) only when its years are in the range.
 */
import { useT } from "../lib/i18n";

/** Years as runs: 2015–2022, 2024. */
export function yearSpan(ys: number[] | undefined): string {
  if (!ys?.length) return "–";
  const sorted = [...ys].sort((a, b) => a - b);
  const runs: string[] = [];
  let start = sorted[0];
  let prev = sorted[0];
  for (const y of [...sorted.slice(1), Number.NaN]) {
    if (y === prev + 1) { prev = y; continue; }
    runs.push(start === prev ? `${start}` : `${start}–${prev}`);
    start = y;
    prev = y;
  }
  return runs.join(", ");
}

/** A figure's range over the price years, as `min`–`max` and its p10–p90, each through `fmt`. */
export function rangeParams(s: any, fmt: (v: number) => string) {
  const r = s?.range;
  return r ? { min: fmt(r.min), max: fmt(r.max), p10: fmt(r.p10), p90: fmt(r.p90) } : null;
}

export function PriceBasis({ P }: { P: any }) {
  const t = useT();
  if (!P) return null;
  const bySource = (key: string) => (P.sources ?? []).filter((s: any) => s.source === key).map((s: any) => s.year);
  const elpris = bySource("elprisetjustnu");
  const entsoe = bySource("entsoe");
  return (
    <p className="caption-muted">
      {t(P.range_is_central ? "prices.basisOnly" : "prices.basis", { central: yearSpan(P.years_central), range: yearSpan(P.years_range) })}{" "}
      {elpris.length > 0 && t("prices.src.elprisetjustnu", { years: yearSpan(elpris) })}
      {entsoe.length > 0 && <> {t("prices.src.entsoe", { years: yearSpan(entsoe) })}</>}
    </p>
  );
}
