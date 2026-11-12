/**
 * The opening answer of Static vs envelopes (DESIGN.md, "The opening answer"): three stops read
 * from /hosting, each a published median.
 *
 * 1. the static study, the worst hour;
 * 2. what can be issued today, the screen's display figure: a range from the hourly study and the
 *    envelopes issued the day before (foresight.json); with only the hourly study published, that
 *    one figure;
 * 3. the perfect-foresight bound, which nobody can issue, marked with the budget it is held to,
 *    and the per-installation figure beside it.
 *
 * Each stop draws its length against the bound under its figure, so the distances are seen as well
 * as said. The ratio line under them is what can be issued over the static study, never the bound's.
 *
 * It reads /hosting itself, so the screen and a test get the same answer from the same request.
 */
import { Term } from "./Term";
import { useApi } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

export interface Stops {
  staticKw: number;
  /** the issuable range, low and high; equal when only the hourly study is published */
  issuable: [number, number];
  /** the houses with solar at each end of the issuable range, when published */
  issuableHouses: [number, number] | null;
  /** the day-ahead median, when foresight.json is published */
  dayAheadKw: number | null;
  boundKw: number;
  /** the same search held to the budget per installation, when published */
  perInstallationKw: number | null;
  zStar: number;
  /** what the bound's budget counts: "aggregate" (the neighbourhood's energy) or "installation" */
  budget: string;
}

/** The three stops from a /hosting payload, or null while the payload lacks the headline. */
export function heroStops(H: any): Stops | null {
  const m = H?.summary?.measures;
  if (!m?.static || !m?.doe) return null;
  const ts: number = m.timeseries?.kw_ac?.median ?? m.static.kw_ac.median;
  const audited = H.foresight?.violation_hours === 0 && H.foresight?.planning_full_year_violations === 0;
  const da: number | null = audited ? H.foresight.median_kw?.climatological ?? null : null;
  const lo = da == null ? ts : Math.min(ts, da);
  const hi = da == null ? ts : Math.max(ts, da);
  const tsHouses: number | null = m.timeseries?.adopters?.median ?? null;
  const daHouses: number | null = da == null ? null : H.foresight.median_adopters?.climatological ?? null;
  const houses = [tsHouses, daHouses].filter((n): n is number => n != null);
  return {
    staticKw: m.static.kw_ac.median,
    issuable: [lo, hi],
    issuableHouses: houses.length ? [Math.min(...houses), Math.max(...houses)] : null,
    dayAheadKw: da,
    boundKw: m.doe.kw_ac.median,
    perInstallationKw: m.doe_installation?.kw_ac?.median ?? null,
    zStar: H.summary.z_star_pct,
    budget: H.summary.budget_basis?.doe ?? "aggregate",
  };
}

export function AnswerStops() {
  const t = useT();
  const f = useFmt();
  const hosting = useApi<any>("/hosting");
  const s = heroStops(hosting.data?.data);
  if (!s) return null;
  const [lo, hi] = s.issuable;
  const range = Math.abs(hi - lo) < 0.5 ? f(lo) : `${f(lo)}–${f(hi)}`;
  const pct = (v: number) => `${(v / s.boundKw) * 100}%`;
  // what can be issued over the static study, at each end of the range, to one decimal
  const [rLo, rHi] = [f(lo / s.staticKw, 1), f(hi / s.staticKw, 1)];
  const ratio = rLo === rHi ? rLo : `${rLo}–${rHi}`;
  return (
    <>
      {/* each stop carries its own length against the bound, so the line reads as figures and as
          distances at once; the words are said once, beside the figure */}
      <ol className="answer-stops" aria-label={t("cmp.stops")}>
        <li className="as-stop as-static">
          <span className="as-value" data-stop="static">{f(s.staticKw)}<small>kW</small></span>
          <i aria-hidden="true" className="as-bar" style={{ width: pct(s.staticKw) }} />
          <span className="as-label"><Term id="static">{t("cmp.fig.static")}</Term></span>
        </li>
        <li className="as-stop as-issuable">
          <span className="as-value" data-stop="issuable">{range}<small>kW</small></span>
          <i aria-hidden="true" className="as-bar" style={{ width: pct(hi), ["--lo" as string]: `${(lo / hi) * 100}%` }} />
          <span className="as-label">{t("cmp.stop.issuable")}</span>
          <span className="as-note">{t(s.dayAheadKw == null ? "cmp.stop.issuableNoteTs" : "cmp.stop.issuableNote")}</span>
        </li>
        <li className="as-stop as-bound">
          <span className="as-value" data-stop="bound">{f(s.boundKw)}<small>kW</small></span>
          <i aria-hidden="true" className="as-bar" />
          <span className="as-label">
            <Term id="foresight">{t("cmp.stop.bound")}</Term>
            <span className="basis-mark">{t(`cmp.stop.basis.${s.budget}`, { z: f(s.zStar) })}</span>
          </span>
          {s.perInstallationKw != null && (
            <span className="as-note" data-stop="installation">{t("cmp.stop.perInstallation", { kw: f(s.perInstallationKw), z: f(s.zStar) })}</span>
          )}
        </li>
      </ol>
      <p className="answer-ratio" data-stop="ratio">{t("cmp.ratioLine", { r: ratio })}</p>
    </>
  );
}
