import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { AnswerStops, heroStops } from "../components/AnswerStops";
import { Deferral } from "../components/Business";
import { PriceBasis, rangeParams } from "../components/PriceBasis";
import { Chart } from "../components/LazyChart";
import { axisStyle } from "../lib/chartStyle";
import { ChartSkeleton, PageSkeleton } from "../components/Skeleton";
import { SectionBar, type Section } from "../components/SectionBar";
import { SectionMissing, ChartOrTable, Legend, Missing, Protocol, Tile } from "../components/bits";
// the map library is the heaviest thing on this screen: it loads beside the words, not before them
const NetworkMap = lazy(() => import("../components/NetworkMap").then((m) => ({ default: m.NetworkMap })));
import { Term } from "../components/Term";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { hourLabel, hoursInYear, median, monthNames, monthStarts, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

const MEASURES = ["static", "fixed_cap", "timeseries", "doe"] as const;

export default function Compare() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const hosting = useApi<any>("/hosting");
  // the heatmaps are a third of the showcase and drawn far down: they load as their section nears
  const show = useApi<any>("/showcase?heatmaps=false");
  const whenRef = useRef<HTMLElement | null>(null);
  const [whenNear, setWhenNear] = useState(false);
  const heatmaps = useApi<any>(whenNear ? "/showcase/heatmaps" : null);
  const risk = useApi<any>("/risk");
  const val = useApi<any>("/validation");
  // the criteria the protocol holds the results against are model inputs, not constants here
  const assumptions = useApi<any>("/assumptions");
  // the hour the transformer is most loaded without control, which the neighbourhood opens on
  const meta = useApi<any>("/meta");
  const net = useApi<any>("/network");
  // what the extra solar is worth, the reinforcement it avoids, and the phase-by-phase check
  const over = useApi<any>("/overinstallation");
  const reinf = useApi<any>("/reinforcement");
  const phases = useApi<any>("/phases");
  const trafoMva: number | null =
    net.data?.features?.find((x: any) => x.properties?.layer === "substation")?.properties?.trafo_sn_mva ?? null;
  const [heat, setHeat] = useState<"limited" | "unc">("limited");

  const H = hosting.data?.data;
  const S = show.data?.data?.showcase;
  const HM = heatmaps.data?.data;
  const R = risk.data?.data;
  const PF = val.data?.data?.powerflow;
  const OV = over.data?.data;
  const RF = reinf.data?.data;
  const PH = phases.data?.data;

  useEffect(() => {
    const el = whenRef.current;
    if (!el || whenNear) return;
    if (typeof IntersectionObserver === "undefined") return setWhenNear(true);
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && setWhenNear(true),
      { rootMargin: "800px 0px" });
    io.observe(el);
    return () => io.disconnect();
  });

  const zDoeMedian = useMemo(() => {
    if (!H) return null;
    return median(H.summary.per_order.map((o: any) => o.z_doe_pct));
  }, [H]);

  // the protocol's rows come from three answers: wait for all of them, so the table does not
  // grow under the reader
  if (hosting.isLoading || show.isLoading || assumptions.isLoading || val.isLoading) return <PageSkeleton bar hero blocks={2} />;
  if (!H || !S) return <Missing of={[hosting, show, risk, val]} />;

  const m = H.summary.measures;
  // the houses the issuable range connects: the hourly study's and, when published, the day-ahead envelopes'
  const stops = heroStops(H);
  const [hLo, hHi] = stops?.issuableHouses ?? [m.timeseries.adopters.median, m.timeseries.adopters.median];
  const issuableHouses = hLo === hHi ? f(hLo) : `${f(hLo)}–${f(hHi)}`;
  const zStar = H.summary.z_star_pct;
  const years = H.summary.years;
  const pol = S.policies;
  const ver = S.verification;
  const fixedZ = (H.curve.points as any[]).map((p) => p.z_fixed_pct).filter((v: number) => v > 0);
  const limit = (key: string): number | null =>
    (assumptions.data?.assumptions as any[] | undefined)?.find((a) => a.key === key)?.value ?? null;
  const vMax = limit("v_max_pu");
  const thermal = limit("thermal_limit_pct");

  // only the sections that have data behind them, so the bar never points at nothing
  // three regions rest on one adoption order, not on the median the rest of the screen shows:
  // each says so in its head, and the bar's basis follows while it is being read
  const priceSpan = (ys: number[] | undefined) => (ys?.length ? `${ys[0]}–${ys[ys.length - 1]}` : "–");
  const curtRange = rangeParams(RF?.envelopes?.spread?.curtailed_sek, (v) => f(v));
  const valueBasis = OV ? t("cmp.basis.value", { order: OV.order, span: priceSpan(OV.price_years) }) : undefined;
  // Or reinforce prices the deployment the perfect-foresight search sized: it says so
  const reinfBasis = RF ? t(RF.basis ? "cmp.basis.reinfBound" : "cmp.basis.reinf", { order: RF.order, z: f(RF.basis?.z_star_pct ?? zStar) }) : undefined;
  const sensBasis = H.tornado ? t("cmp.basis.sens", { order: H.tornado.order, n: H.tornado.years?.length ?? years.length }) : undefined;
  const sections: Section[] = [
    { id: "capacity", label: t("cmp.sec.capacity") },
    { id: "verify", label: t("cmp.sec.verify") },
    ...(S ? [{ id: "when", label: t("cmp.sec.when") }] : []),
    { id: "fair", label: t("cmp.sec.fair") },
    ...(OV ? [{ id: "value", label: t("cmp.sec.value"), basis: valueBasis }] : []),
    ...(RF ? [{ id: "reinforce", label: t("cmp.sec.reinforce"), basis: reinfBasis }] : []),
    ...(H.tornado ? [{ id: "sensitivity", label: t("cmp.sec.sensitivity"), basis: sensBasis }] : []),
  ];

  return (
    <div className="page">
      <SectionBar
        sections={sections}
        basis={<Term id="sample">{t("overview.sample", { n: H.summary.n_orders, years: years.length })}</Term>}
      />
      <div className="page-inner">
        {/* ---------------------------------------------------------------- the answer */}
        <section className="region">
          <div className="overview-hero">
            <div className="overview-copy">
              <h1 className="headline">{t("overview.title")}</h1>
              <p className="headline-sub">{t("cmp.leadShort", { mva: trafoMva != null ? f(trafoMva, 1) : "–" })}</p>
              {/* the screen's one answer: static, what can be issued today, the perfect-foresight
                  bound; each figure is said here and nowhere else in the view */}
              <AnswerStops />
              {/* the one action follows the answer, so it is in the first viewport at 1536×730; the
                  data sheet under it is the answer's evidence */}
              <div className="hero-actions">
                <a className="btn" href="#/request">{t("overview.request")}</a>
                <a className="btn ghost" href="#/twin">{t("overview.explore")}</a>
              </div>
              <div className="tiles cols-1 answer-sheet">
                <Tile label={t("cmp.sheet.houses")} value={`${f(m.static.adopters.median)} → ${issuableHouses}`}
                  note={t("cmp.sheet.housesNote", { n: f(m.doe.adopters.median) })} />
                <Tile label={<Term id="curtailment">{t("cmp.sheet.cost")}</Term>} value={f(zDoeMedian, 1)} unit={t("cmp.sheet.costUnit")} />
                <Tile label={t("cmp.sheet.overHours", { year: S.year })} value={f(ver.uncontrolled_violation_hours)} unit="h" tone="danger" />
                <Tile
                  label={t("cmp.sheet.peak")}
                  value={f(ver.uncontrolled_max_trafo_loading_pct)}
                  unit={t("cmp.sheet.peakUnit")}
                  tone="danger"
                  note={typeof meta.data?.showcase_hour === "number"
                    ? <a href={`#/twin?hour=${meta.data.showcase_hour}&scenario=uncontrolled`}>{hourLabel(lang, S.year, meta.data.showcase_hour)}</a>
                    : undefined}
                />
              </div>
            </div>
            <Suspense fallback={<div className="net-figure" aria-hidden="true"><span /><div className="net-map" /></div>}>
              <NetworkMap />
            </Suspense>
          </div>
        </section>

        {/* ---------------------------------------------------------------- capacity */}
        <section className="region" id="capacity">
          <div className="region-head">
            <div className="region-title">
              <h2><Term id="hosting">{t("cmp.bars")}</Term></h2>
              <p className="caption">{t("cmp.bars.caption")}</p>
            </div>
          </div>
          <ChartOrTable
            chart={<Chart ariaLabel={t("cmp.bars")} height={250} deps={[H]} build={(tk, width) => barsOption(tk, m, (k) => measureLabel(t, k, zStar), width)} />}
            table={
              <table className="data">
                <thead><tr><th>{t("tbl.method")}</th><th className="r">{t("tbl.kwMedian")}</th><th className="r">P10</th><th className="r">P90</th><th className="r">{t("tbl.houses")}</th></tr></thead>
                <tbody>
                  {MEASURES.map((k) => (
                    <tr key={k}><td>{measureLabel(t, k, zStar)}</td><td className="r">{f(m[k].kw_ac.median)}</td><td className="r">{f(m[k].kw_ac.p10)}</td><td className="r">{f(m[k].kw_ac.p90)}</td><td className="r">{f(m[k].adopters.median)}</td></tr>
                  ))}
                </tbody>
              </table>
            }
          />
          {/* the cost of the last kilowatt is a figure in its own right, so it gets a panel */}
          <section className="block">
            <div className="block-head"><h3>{t("cmp.curve")}</h3></div>
            <p className="caption">{t("cmp.curve.caption", { from: years[0], to: years[years.length - 1] })}</p>
            <ChartOrTable
              chart={<Chart ariaLabel={t("cmp.curve")} height={300} deps={[H]} build={(tk, _w, fm) => curveOption(tk, H, t, zStar, fm)} />}
              table={
                <table className="data">
                  <thead><tr><th className="r">{t("tbl.kwAc")}</th><th className="r">{t("tbl.houses")}</th><th className="r">{t("cmp.curve.doe")}, %</th><th className="r">{t("tbl.yearRange")}, %</th><th className="r">{t("cmp.curve.fixed")}, %</th></tr></thead>
                  <tbody>
                    {(H.curve.points as any[]).map((p) => (
                      <tr key={p.adopters}><td className="r">{f(p.kw_ac)}</td><td className="r">{f(p.adopters)}</td><td className="r">{f(p.z_doe_pct, 2)}</td><td className="r">{f(Math.min(...p.z_doe_by_year), 1)}–{f(Math.max(...p.z_doe_by_year), 1)}</td><td className="r">{f(p.z_fixed_pct, 1)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
            {fixedZ.length > 0 && (
              <p className="caption">
                {t("cmp.curve.fixedNote", { a: f(Math.min(...fixedZ)), b: f(Math.max(...fixedZ)) })}
              </p>
            )}
          </section>
        </section>

        {/* ---------------------------------------------------------------- verification */}
        <section className="region" id="verify">
          <div className="region-head">
            <div className="region-title"><h2>{t("cmp.verify")}</h2></div>
          </div>
          <Protocol
            rows={[
              { ok: ver.doe_full_year_violations === 0, label: t("cmp.verify.doe"), value: f(ver.doe_full_year_violations), unit: "h", criterion: t("cmp.verify.of", { h: f(hoursInYear(S.year)), year: S.year }) },
              ...(vMax != null ? [{ ok: ver.doe_max_voltage_pu <= vMax, label: t("cmp.verify.vmax"), value: f(ver.doe_max_voltage_pu, 3), unit: "pu", criterion: t("cmp.verify.limit", { v: `${f(vMax, 2)} pu` }) }] : []),
              ...(thermal != null ? [{ ok: ver.doe_max_trafo_loading_pct <= thermal, label: t("cmp.verify.trafo"), value: f(ver.doe_max_trafo_loading_pct, 1), unit: "%", criterion: t("cmp.verify.limit", { v: `${f(thermal)} %` }) }] : []),
              { ok: ver.screen_missed_hours === 0, label: t("cmp.verify.screen"), value: f(ver.screen_missed_hours), unit: "h", criterion: t("cmp.verify.candidates", { n: f(ver.screen_candidate_hours) }) },
              ...(PF ? [{ ok: null, label: t("cmp.verify.zbus"), value: PF.max_abs_dv_pu.toExponential(0), unit: "pu", criterion: t("cmp.verify.zbusNote", { n: PF.cases }) }] : []),
              { ok: false, label: t("cmp.verify.unc"), value: f(ver.uncontrolled_violation_hours), unit: "h", criterion: t("cmp.verify.uncNote", { year: S.year }) },
            ]}
          />
          <p className="caption">{t("cmp.binding", { v: f(ver.uncontrolled_max_voltage_pu, 3) })}</p>
        </section>

        {/* ---------------------------------------------------------------- when it binds */}
        {S && (
          <section className="region" id="when" ref={whenRef}>
            <div className="region-head">
              <div className="region-title">
                <h2>{t("cmp.heat", { year: HM?.year ?? S.year })}</h2>
                <p className="caption">{t("cmp.heat.caption")}</p>
              </div>
              <div className="seg" role="group" aria-label={t("cmp.heat.view")}>
                <button aria-pressed={heat === "limited"} onClick={() => setHeat("limited")}>{t("cmp.heat.limited")}</button>
                <button aria-pressed={heat === "unc"} onClick={() => setHeat("unc")}>{t("scn.uncontrolled")}</button>
              </div>
            </div>
            {/* the dense band of this screen: a year of hours, on its own ground */}
            <div className="well">
              {HM ? (
                <ChartOrTable
                  chart={<Chart ariaLabel={t("cmp.heat", { year: HM.year })} height={260} deps={[HM, heat, lang]} build={(tk, _w, fm) => heatOption(tk, HM, heat, lang, t, fm)} />}
                  table={<HeatTable HM={HM} which={heat} />}
                />
              ) : <ChartSkeleton height={260} />}
            </div>
          </section>
        )}

        {/* ---------------------------------------------------------------- fairness */}
        <section className="region" id="fair">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("cmp.fair")}</h2>
              <p className="caption">{t("cmp.fair.caption")}</p>
            </div>
            <Legend items={[
              { color: "var(--series-dynamic)", label: t("pol.maxmin") },
              { color: "var(--cat-1)", label: t("pol.prorata") },
            ]} />
          </div>
          <ChartOrTable
            chart={<Chart ariaLabel={t("cmp.fair")} height={270} deps={[S]} build={(tk, _w, fm) => fairOption(tk, pol, t, fm)} />}
            table={
              <table className="data">
                <thead><tr><th>{t("tbl.policy")}</th><th className="r">{t("tbl.curtailment")}</th><th className="r">{t("tbl.worst")}</th><th className="r">{t("tbl.jain")}</th><th className="r">{t("tbl.custHours")}</th></tr></thead>
                <tbody>
                  {Object.entries(pol).map(([k, v]: any) => (
                    <tr key={k}><td>{t(`pol.${k}`)}</td><td className="r">{f(v.z_pct, 2)}</td><td className="r">{f(v.max_customer_z_pct, 1)}</td><td className="r">{f(v.jain_index, 2)}</td><td className="r">{f(v.customer_hours_limited)}</td></tr>
                  ))}
                </tbody>
              </table>
            }
          />
          <SectionMissing title={t("cmp.risk")} q={risk} />
          {R && (
            <section className="block stack">
              <div className="block-head"><h3>{t("cmp.risk")}</h3></div>
              <p className="caption">{t("cmp.risk.caption")}</p>
              <div className="tiles cols-4">
                <Tile label={t("cmp.risk.mean")} value={f(R.stats.mean_z_pct, 1)} unit="%" />
                <Tile label={t("cmp.risk.p90")} value={f(R.stats.p90_z_pct, 1)} unit="%" />
                <Tile label={t("cmp.risk.worst")} value={f(R.stats.worst_z_pct, 1)} unit="%" />
                <Tile label={<Term id="cvar">{t("cmp.risk.cvar")}</Term>} value={f(R.stats.cvar95_z_pct, 1)} unit="%" />
              </div>
              <ChartOrTable
                chart={<Chart ariaLabel={t("cmp.risk")} height={200} deps={[R]} build={(tk, _w, fm) => riskOption(tk, R, zStar, fm)} />}
                table={
                  <div className="table-window">
                    <table className="data">
                      <thead><tr><th>{t("tbl.year")}</th><th className="r">{t("tbl.curtailment")}</th></tr></thead>
                      <tbody>{(R.cases as any[]).map((c, i) => <tr key={i}><td>{c.year}</td><td className="r">{f(c.z_pct, 2)}</td></tr>)}</tbody>
                    </table>
                  </div>
                }
              />
            </section>
          )}
        </section>

        {/* ---------------------------------------------------------------- value */}
        {!OV && <SectionMissing title={t("cmp.value")} q={over} />}
        {!RF && <SectionMissing title={t("cmp.reinf")} q={reinf} />}
        {OV && (
          <section className="region" id="value">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("cmp.value")}</h2>
                <p className="caption">{t("cmp.value.caption")}</p>
                <p className="region-basis">{valueBasis}</p>
                <PriceBasis P={OV.prices} />
              </div>
              <Legend items={[
                { color: "var(--series-dynamic)", label: t("cmp.value.delivered") },
                { color: "var(--warning-fill)", label: t("cmp.value.curtailed") },
              ]} />
            </div>
            <ChartOrTable
              chart={<Chart ariaLabel={t("cmp.value")} height={260} deps={[OV]} build={(tk, _w, fm) => valueOption(tk, OV, t, fm)} />}
              table={
                <table className="data">
                  <thead><tr><th className="r">{t("tbl.kwAc")}</th><th className="r">{t("tbl.beyondStatic")}</th><th className="r">{t("cmp.value.delivered")}, kSEK</th><th className="r">{t("cmp.value.curtailed")}, kSEK</th></tr></thead>
                  <tbody>
                    {OV.curve.map((p: any) => (
                      <tr key={p.adopters}><td className="r">{f(p.kw_ac)}</td><td className="r">{f(p.kw_ac_nonfirm)}</td><td className="r">{f(p.delivered_sek / 1000, 1)}</td><td className="r">{f(p.curtailed_sek / 1000, 2)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
            <div className="tiles cols-3">
              <Tile label={t("cmp.value.atBudget", { kw: f(OV.budget.kw_ac) })} value={f(OV.budget.delivered_sek / 1000)} unit={t("unit.ksekYr")} note={t("cmp.value.atBudgetNote", { kw: f(OV.budget.kw_ac_nonfirm) })} />
              <Tile label={t("cmp.value.curtAtBudget")} value={f(OV.budget.curtailed_sek / 1000, 1)} unit={t("unit.ksekYr")} />
              {OV.budget_marginal && (
                <Tile label={t("cmp.value.lastKw")} value={f(OV.budget_marginal.energy_share_pct, 0)} unit="%" note={t("cmp.value.lastKwNote", { v: f(OV.budget_marginal.value_share_pct, 0) })} />
              )}
            </div>
          </section>
        )}

        {/* ---------------------------------------------------------------- reinforcement */}
        {RF && (
          <section className="region" id="reinforce">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("cmp.reinf")}</h2>
                <p className="caption">{t("cmp.reinf.caption")}</p>
                <p className="region-basis">{reinfBasis}</p>
              </div>
            </div>
            <div className="reinf">
              <div className="reinf-side">
                <h3>{t("cmp.reinf.env")}</h3>
                <dl className="kv">
                  <dt>{t("cmp.reinf.connected")}</dt><dd>{f(RF.envelopes.kw_ac)} kW</dd>
                  <dt>{t("cmp.reinf.beyond")}</dt><dd>{f(RF.envelopes.nonfirm_kw)} kW</dd>
                  <dt>{t("cmp.reinf.curtCost")}</dt><dd>{f(RF.envelopes.curtailed_sek_per_year)} {t("unit.sekYr")}</dd>
                  <dt>{t("cmp.reinf.delivered")}</dt><dd>{f(RF.envelopes.delivered_sek_per_year)} {t("unit.sekYr")}</dd>
                </dl>
              </div>
              <div className="reinf-side">
                <h3>{t("cmp.reinf.big", { level: RF.bigger.level })}</h3>
                <dl className="kv">
                  <dt><Term id="static">{t("cmp.reinf.static")}</Term></dt><dd>{f(RF.today.static_kw)} → {f(RF.bigger.static_kw)} kW</dd>
                  <dt>{t("cmp.reinf.uncontrolled")}</dt><dd>{f(RF.today.timeseries_kw)} → {f(RF.bigger.timeseries_kw)} kW</dd>
                  <dt>{t("cmp.reinf.cost")}</dt><dd>{f(RF.transformer_sek[0] / 1000)}–{f(RF.transformer_sek[1] / 1000)} kSEK</dd>
                  <dt>{t("cmp.reinf.station")}</dt><dd>+{f(RF.station_sek / 1000)} kSEK</dd>
                </dl>
              </div>
            </div>
            {RF.years_of_curtailment.transformer[0] != null && (
              <p className="lead-answer">{t("cmp.reinf.years", {
                a: f(RF.years_of_curtailment.transformer[0]), b: f(RF.years_of_curtailment.transformer[1]),
                c: f(RF.years_of_curtailment.with_station[0]), d: f(RF.years_of_curtailment.with_station[1]),
              })}</p>
            )}
            {curtRange && <p className="caption">{t("cmp.reinf.curtRange", curtRange)}</p>}
            <PriceBasis P={RF.prices} />
            <p className="caption-muted">{t("cmp.reinf.note", { year: RF.price_level_year, span: priceSpan(RF.envelopes.price_years), order: RF.order })}</p>
            <Deferral />
          </section>
        )}

        {/* ---------------------------------------------------------------- sensitivity */}
        {H.tornado && (
          <section className="region" id="sensitivity">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("cmp.tornado")}</h2>
                <p className="caption">{t("cmp.tornado.caption")}</p>
                <p className="region-basis">{sensBasis}</p>
              </div>
              <Legend items={[
                { color: "var(--series-dynamic)", label: t("cmp.fig.doe") },
                { color: "var(--neutral-series)", label: t("cmp.fig.static") },
              ]} />
            </div>
            <ChartOrTable
              chart={<Chart ariaLabel={t("cmp.tornado")} height={330} deps={[H.tornado]} build={(tk, width, fm) => tornadoOption(tk, H.tornado, t, width, fm)} />}
              table={
                <table className="data">
                  <thead><tr><th>{t("tbl.input")}</th><th>{t("tbl.value")}</th><th className="r">{t("tbl.staticKw")}</th><th className="r">{t("tbl.tsKw")}</th><th className="r">{t("tbl.doeKw")}</th></tr></thead>
                  <tbody>
                    <tr><td>{t("tbl.base")}</td><td>–</td><td className="r">{f(H.tornado.base.static_kw)}</td><td className="r">{f(H.tornado.base.timeseries_kw)}</td><td className="r">{f(H.tornado.base.doe_kw)}</td></tr>
                    {H.tornado.cases.map((c: any) => (
                      <tr key={c.label + c.level}><td>{tornadoLabel(t, c)}</td><td>{tornadoLevel(t, c.level)}</td><td className="r">{f(c.static_kw)}</td><td className="r">{f(c.timeseries_kw)}</td><td className="r">{f(c.doe_kw)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
            <SectionMissing title={t("cmp.phases")} q={phases} />
            {PH && (
              <section className="block stack">
                <div className="block-head"><h3>{t("cmp.phases")}</h3></div>
                <p className="caption">{t("cmp.phases.caption", { order: PH.order, kva: f(PH.single_phase_limit_kva, 1) })}</p>
                <table className="data">
                  <thead><tr><th>{t("tbl.case")}</th><th className="r">{t("tbl.houses")}</th><th className="r">{t("tbl.staticKw")}</th></tr></thead>
                  <tbody>
                    {PH.cases.map((c: any) => (
                      <tr key={c.key}>
                        <td>{t(`phase.${c.key}`)}</td>
                        <td className="r">{f(c.adopters ?? c.adopters_median, c.adopters === undefined ? 1 : 0)}</td>
                        <td className="r">{c.kw_ac !== undefined ? f(c.kw_ac) : t("cmp.phases.range", { m: f(c.kw_ac_median), a: f(c.kw_ac_min), b: f(c.kw_ac_max) })}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function valueOption(tk: Tokens, OV: any, t: (k: string, v?: any) => string, f: (v: number | null | undefined, d?: number) => string) {
  const pts = OV.curve as any[];
  const xs = pts.map((p) => p.kw_ac);
  return {
    grid: { left: 52, right: 24, top: 18, bottom: 44 },
    xAxis: { type: "value", min: Math.floor(Math.min(...xs) / 50) * 50, ...axisStyle(tk), name: "kW AC", nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", ...axisStyle(tk), name: "kSEK", min: 0 },
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const p = pts[ps[0].dataIndex];
        return `<b>${f(p.kw_ac)} kW</b> (${t("cmp.value.tt", { n: f(p.kw_ac_nonfirm) })})<br/>${t("cmp.value.delivered")}: <b>${f(p.delivered_sek / 1000, 1)} kSEK</b><br/>${t("cmp.value.curtailed")}: <b>${f(p.curtailed_sek / 1000, 2)} kSEK</b>`;
      },
    },
    series: [
      {
        type: "line", name: t("cmp.value.delivered"), data: pts.map((p) => [p.kw_ac, p.delivered_sek / 1000]), symbol: "circle", symbolSize: 6,
        lineStyle: { width: 2, color: tk.dynamic }, itemStyle: { color: tk.dynamic, borderColor: tk.surface, borderWidth: 2 }, areaStyle: { color: tk.dynamic, opacity: 0.08 },
        markLine: {
          symbol: "none", silent: true, lineStyle: { color: tk.borderStrong, type: "solid", width: 1 },
          label: { color: tk.text2, fontFamily: tk.font, fontSize: tk.size.caption, position: "insideEndTop", rotate: 90 },
          data: [
            { xAxis: OV.static.kw_ac, label: { formatter: t("m.static") } },
            { xAxis: OV.budget.kw_ac, label: { formatter: t("cmp.value.budget", { z: f(OV.z_star_pct) }) } },
          ],
        },
      },
      {
        type: "line", name: t("cmp.value.curtailed"), data: pts.map((p) => [p.kw_ac, p.curtailed_sek / 1000]), symbol: "circle", symbolSize: 6,
        lineStyle: { width: 2, color: tk.warningFill }, itemStyle: { color: tk.warningFill, borderColor: tk.surface, borderWidth: 2 }, areaStyle: { color: tk.warningFill, opacity: 0.15 },
      },
    ],
  };
}

/** The heatmap's hours as a list: every hour in which someone was limited, or the transformer went past its rating. */
function HeatTable({ HM, which }: { HM: any; which: "limited" | "unc" }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const src: number[][] = which === "limited" ? HM.limited_customers : HM.uncontrolled_trafo_loading;
  const rows: { d: number; h: number; v: number }[] = [];
  for (let d = 0; d < HM.days; d++) for (let h = 0; h < 24; h++) {
    const v = src[h][d];
    if (which === "limited" ? v > 0 : v > 100) rows.push({ d, h, v });
  }
  return (
    <div className="table-window">
      <table className="data">
        <thead><tr><th>{t("tbl.hour")}</th><th className="r">{which === "limited" ? t("cmp.heat.limited") : `${t("twin.trafo")}, %`}</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.d}-${r.h}`}><td>{hourLabel(lang, HM.year, r.d * 24 + r.h)}</td><td className="r">{f(r.v)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A sensitivity case in the interface's language; a case this version does not know keeps the engine's words. */
function tornadoLabel(t: (k: string) => string, c: { key: string; label: string }): string {
  const s = t(`tor.${c.key}`);
  return s === `tor.${c.key}` ? c.label : s;
}

function tornadoLevel(t: (k: string) => string, level: string): string {
  return level.replace("(no load)", t("tor.noLoad"));
}

function measureLabel(t: (k: string, v?: any) => string, k: string, z: number) {
  return k === "doe" ? t("m.doe", { z }) : t(`m.${k}`);
}

// ------------------------------------------------------------------------------ chart options

function barsOption(tk: Tokens, m: any, label: (k: string) => string, width: number) {
  const compact = width < 520;
  const cats = MEASURES.map(label);
  const vals = MEASURES.map((k) => m[k].kw_ac.median);
  const lo = MEASURES.map((k) => m[k].kw_ac.p10);
  const hi = MEASURES.map((k) => m[k].kw_ac.p90);
  return {
    grid: { left: compact ? 116 : 250, right: compact ? 58 : 70, top: 10, bottom: 30 },
    xAxis: { type: "value", ...axisStyle(tk), name: "kW", nameLocation: "end" },
    yAxis: { type: "category", data: cats, inverse: true, ...axisStyle(tk), axisLabel: { color: tk.text, fontFamily: tk.font, fontSize: compact ? tk.size.caption : tk.size.small, width: compact ? 106 : 240, overflow: "break" } },
    tooltip: { trigger: "item", formatter: (p: any) => `${cats[p.dataIndex]}<br/><b>${Math.round(vals[p.dataIndex])} kW</b> (P10 ${Math.round(lo[p.dataIndex])}–P90 ${Math.round(hi[p.dataIndex])})` },
    series: [
      {
        type: "bar",
        barWidth: 22,
        data: vals.map((v, i) => ({ value: v, itemStyle: { color: MEASURES[i] === "doe" ? tk.dynamic : tk.neutralSeries, borderRadius: [0, tk.size.radius, tk.size.radius, 0] } })),
        label: { show: true, position: "right", distance: compact ? 8 : 26, fontFamily: tk.font, fontSize: compact ? tk.size.caption : tk.size.small, color: tk.text, fontWeight: 500, formatter: (p: any) => `${Math.round(p.value)} kW` },
        z: 2,
      },
      {
        type: "custom",
        renderItem: (_p: any, api: any) => {
          const i = api.value(0);
          const a = api.coord([lo[i], i]);
          const b = api.coord([hi[i], i]);
          return {
            type: "group",
            children: [
              { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: tk.text, lineWidth: 1.5 } },
              { type: "line", shape: { x1: a[0], y1: a[1] - 5, x2: a[0], y2: a[1] + 5 }, style: { stroke: tk.text, lineWidth: 1.5 } },
              { type: "line", shape: { x1: b[0], y1: b[1] - 5, x2: b[0], y2: b[1] + 5 }, style: { stroke: tk.text, lineWidth: 1.5 } },
            ],
          };
        },
        data: MEASURES.map((_, i) => [i]),
        encode: { x: -1, y: 0 },
        z: 3,
        tooltip: { show: false },
      },
    ],
  };
}

function curveOption(tk: Tokens, H: any, t: (k: string, v?: any) => string, zStar: number, f: (v: number | null | undefined, d?: number) => string) {
  const pts = H.curve.points as any[];
  const xs = pts.map((p) => p.kw_ac);
  const minY = pts.map((p) => Math.min(...p.z_doe_by_year));
  const maxY = pts.map((p) => Math.max(...p.z_doe_by_year));
  const m = H.summary.measures;
  const marks = [
    { x: m.static.kw_ac.median, name: t("m.static") },
    { x: m.timeseries.kw_ac.median, name: t("m.timeseries") },
    { x: m.doe.kw_ac.median, name: t("cmp.fig.doe") },
  ];
  return {
    grid: { left: 52, right: 24, top: 18, bottom: 44 },
    xAxis: { type: "value", min: Math.floor(Math.min(...xs) / 50) * 50, ...axisStyle(tk), name: "kW AC", nameLocation: "middle", nameGap: 28 },
    yAxis: { type: "value", ...axisStyle(tk), axisLabel: { color: tk.muted, formatter: "{value} %" } },
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const i = ps[0].dataIndex;
        const p = pts[i];
        return `<b>${f(p.kw_ac)} kW</b>, ${t("cmp.tt.houses", { n: f(p.adopters) })}<br/>${t("cmp.curve.doe")}: <b>${f(p.z_doe_pct, 2)} %</b> (${f(minY[i], 1)}–${f(maxY[i], 1)} %)<br/>${t("cmp.curve.fixed")}: <b>${f(p.z_fixed_pct, 1)} %</b>`;
      },
    },
    series: [
      { type: "line", data: xs.map((x, i) => [x, minY[i]]), stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { type: "line", data: xs.map((x, i) => [x, maxY[i] - minY[i]]), stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: tk.dynamic, opacity: 0.12 }, silent: true, tooltip: { show: false } },
      {
        type: "line", name: t("cmp.curve.doe"), data: xs.map((x, i) => [x, pts[i].z_doe_pct]), symbol: "circle", symbolSize: 7,
        lineStyle: { width: 2, color: tk.dynamic }, itemStyle: { color: tk.dynamic, borderColor: tk.surface, borderWidth: 2 },
        markLine: {
          symbol: "none", silent: true,
          lineStyle: { color: tk.borderStrong, type: "solid", width: 1 },
          label: { color: tk.text2, fontFamily: tk.font, fontSize: tk.size.caption },
          data: [
            { yAxis: zStar, label: { formatter: t("cmp.curve.limit", { z: zStar }), position: "insideStartTop" } },
            ...marks.map((mk) => ({ xAxis: mk.x, label: { formatter: mk.name, position: "insideEndTop", rotate: 90 } })),
          ],
        },
      },
    ],
  };
}

function heatOption(tk: Tokens, HM: any, which: "limited" | "unc", lang: string, t: (k: string) => string, f: (v: number | null | undefined, d?: number) => string) {
  const src: number[][] = which === "limited" ? HM.limited_customers : HM.uncontrolled_trafo_loading;
  const data: [number, number, number][] = [];
  let vmax = 0;
  for (let h = 0; h < 24; h++) for (let d = 0; d < HM.days; d++) {
    const v = src[h][d];
    if (which === "limited" && v <= 0) continue;
    data.push([d, h, v]);
    vmax = Math.max(vmax, v);
  }
  const months = monthNames(lang);
  const starts = monthStarts(HM.year);
  return {
    grid: { left: 44, right: 80, top: 8, bottom: 30 },
    xAxis: {
      type: "category", data: Array.from({ length: HM.days }, (_, i) => i), ...axisStyle(tk), splitLine: { show: false },
      axisLabel: { color: tk.muted, interval: (i: number) => starts.includes(i), formatter: (v: string) => months[starts.indexOf(Number(v))] ?? "" },
    },
    yAxis: { type: "category", data: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}:00`), inverse: true, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 5 } },
    visualMap: which === "limited"
      ? {
          min: 1, max: Math.max(1, vmax), calculable: false, orient: "vertical", right: 0, top: "middle",
          itemHeight: 140, itemWidth: 10,
          textStyle: { color: tk.muted, fontFamily: tk.font },
          // a count of houses held back: the amber of "limited", faint for one, full for the most;
          // an alpha ramp, because the amber wash is as light as the well this chart sits on
          inRange: { color: [tk.warningFill], colorAlpha: [0.35, 1] },
          text: [f(vmax), f(1)],
        }
      : {
          // the loading ramp's own stops, and one flat colour above the rating (DESIGN.md)
          type: "piecewise", orient: "vertical", right: 0, top: "middle", itemWidth: 10, itemHeight: 10,
          textStyle: { color: tk.muted, fontFamily: tk.font },
          pieces: [
            { gt: 100, color: tk.loadOver, label: "> 100 %" },
            { gte: 85, lte: 100, color: tk.load[2], label: "85–100 %" },
            { gte: 50, lt: 85, color: tk.load[1], label: "50–85 %" },
            { gte: 0, lt: 50, color: tk.load[0], label: "0–50 %" },
          ],
        },
    tooltip: {
      formatter: (p: any) => {
        const [d, h, v] = p.value;
        const date = new Date(Date.UTC(HM.year, 0, 1) + d * 86400000);
        const label = `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${String(h).padStart(2, "0")}:00`;
        return which === "limited" ? `${label}<br/>${t("cmp.heat.limited")}: <b>${f(v)}</b>` : `${label}<br/>${t("twin.trafo")}: <b>${f(v)} %</b>`;
      },
    },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: tk.surface, borderWidth: 0 }, progressive: 0 }],
  };
}

function fairOption(tk: Tokens, pol: any, t: (k: string, v?: any) => string, f: (v: number | null | undefined, d?: number) => string) {
  const mk = (k: string, color: string) => {
    const pc = pol[k].per_customer;
    return {
      type: "scatter", name: t(`pol.${k}`), symbolSize: 9,
      itemStyle: { color, borderColor: tk.surface, borderWidth: 2 },
      data: pc.dist_m.map((d: number, i: number) => [d, pc.z_pct[i], pc.cid[i]]),
    };
  };
  return {
    grid: { left: 52, right: 20, top: 16, bottom: 44 },
    xAxis: { type: "value", ...axisStyle(tk), name: "m", nameLocation: "middle", nameGap: 28, scale: true },
    yAxis: { type: "value", ...axisStyle(tk), axisLabel: { color: tk.muted, formatter: "{value} %" } },
    tooltip: { trigger: "item", formatter: (p: any) => `${p.seriesName}<br/>${t("cmp.tt.house", { id: p.value[2], m: f(p.value[0]) })}<br/>${t("cmp.tt.curtailed")}: <b>${f(p.value[1], 2)} %</b>` },
    series: [mk("maxmin", tk.dynamic), mk("prorata", tk.cat[0])],
  };
}

function riskOption(tk: Tokens, R: any, zStar: number, f: (v: number | null | undefined, d?: number) => string) {
  const cases = R.cases as any[];
  const years = Array.from(new Set(cases.map((c) => c.year))).sort();
  return {
    grid: { left: 44, right: 16, top: 12, bottom: 28 },
    xAxis: { type: "category", data: years.map(String), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), axisLabel: { color: tk.muted, formatter: "{value} %" } },
    tooltip: { trigger: "item", formatter: (p: any) => `${p.value[0]}<br/><b>${f(p.value[1], 2)} %</b>` },
    series: [{
      type: "scatter", symbolSize: 8, itemStyle: { color: tk.dynamic, borderColor: tk.surface, borderWidth: 2 },
      data: cases.map((c) => [String(c.year), c.z_pct]),
      markLine: { symbol: "none", silent: true, lineStyle: { color: tk.borderStrong, type: "solid" }, label: { color: tk.text2, fontFamily: tk.font, formatter: `${f(zStar)} %`, position: "insideEndTop" }, data: [{ yAxis: zStar }] },
    }],
  };
}

function tornadoOption(tk: Tokens, T: any, t: (k: string) => string, width: number, f: (v: number | null | undefined, d?: number) => string) {
  const compact = width < 520;
  const groups: Record<string, any[]> = {};
  for (const c of T.cases) (groups[tornadoLabel(t, c)] ||= []).push(c);
  const labels = Object.keys(groups);
  const rng = (key: "doe_kw" | "static_kw") => labels.map((l) => {
    const vs = groups[l].map((c) => c[key]);
    return [Math.min(...vs), Math.max(...vs)];
  });
  const d = rng("doe_kw");
  const s = rng("static_kw");
  const levels = (l: string) => groups[l].map((c) => `${tornadoLevel(t, c.level)}: ${t("m.static")} ${f(c.static_kw)} kW, ${t("cmp.fig.doe")} ${f(c.doe_kw)} kW`).join("<br/>");
  return {
    grid: { left: compact ? 116 : 250, right: compact ? 20 : 40, top: 10, bottom: 34 },
    xAxis: { type: "value", ...axisStyle(tk), name: "kW AC", nameLocation: "middle", nameGap: 24, min: 0 },
    yAxis: { type: "category", data: labels, inverse: true, ...axisStyle(tk), axisLabel: { color: tk.text, fontFamily: tk.font, fontSize: compact ? tk.size.caption : tk.size.small, width: compact ? 106 : 240, overflow: "truncate" } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (ps: any[]) => `<b>${ps[0].name}</b><br/>${levels(ps[0].name)}` },
    series: [
      { type: "bar", stack: "d", data: d.map((x) => x[0]), itemStyle: { color: "transparent" }, barWidth: 10, silent: true },
      { type: "bar", stack: "d", data: d.map((x) => Math.max(x[1] - x[0], 2)), itemStyle: { color: tk.dynamic, borderRadius: tk.size.radius }, barWidth: 10 },
      { type: "bar", stack: "s", data: s.map((x) => x[0]), itemStyle: { color: "transparent" }, barWidth: 10, silent: true },
      { type: "bar", stack: "s", data: s.map((x) => Math.max(x[1] - x[0], 2)), itemStyle: { color: tk.neutralSeries, borderRadius: tk.size.radius }, barWidth: 10 },
      { type: "scatter", data: labels.map((l) => [T.base.doe_kw, l]), symbolSize: 9, itemStyle: { color: tk.text, borderColor: tk.surface, borderWidth: 2 }, z: 5, silent: true },
      { type: "scatter", data: labels.map((l) => [T.base.static_kw, l]), symbolSize: 9, itemStyle: { color: tk.text2, borderColor: tk.surface, borderWidth: 2 }, z: 5, silent: true },
    ],
  };
}

