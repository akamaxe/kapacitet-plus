import { NetworkCheck } from "../components/NetworkCheck";
import { NetworksPanel } from "../components/Networks";
import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { ChartSkeleton, PageSkeleton } from "../components/Skeleton";
import { SectionBar, type Section } from "../components/SectionBar";
import { DataTable, type Col } from "../components/DataTable";
import { SectionMissing, Protocol, ChartOrTable, Chip, Legend, Missing, Status, Tile } from "../components/bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { clock, dateTime, listJoin, monthLong, round, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

/** One entry of config/assumptions.toml, as /assumptions serves it. */
interface Input {
  key: string;
  label: string;
  label_sv?: string;
  note_sv?: string;
  value: number | string | (number | string)[];
  unit?: string;
  tag: string;
  source: string;
  url?: string;
  note?: string;
}

const TAGS = ["sourced", "regulation", "library", "derived", "team-assumption"];

/** An input's label or note in the reader's language, English when it has none. */
function localized(a: Input, field: "label" | "note", lang: string): string {
  const own = lang === "en" ? undefined : (a as unknown as Record<string, string | undefined>)[`${field}_${lang}`];
  return own || (a[field] ?? "");
}

/** How many limitation notes the dictionaries carry, met.limit.1 onwards. */
const N_LIMITS = 9;


const REFS = [
  ["Bollen, M. & Hassan, F. (2011). Integration of Distributed Generation in the Power System. Wiley–IEEE Press.", ""],
  ["Petrou, K. et al. (2020). Operating envelopes for prosumers in LV networks: a weighted proportional fairness approach. IEEE PES ISGT-Europe.", ""],
  ["Liu, M. Z. et al. (2022). Using OPF-based operating envelopes to facilitate residential DER services. IEEE Trans. Smart Grid.", "https://www.researchgate.net/publication/361412635_Using_OPF-Based_Operating_Envelopes_to_Facilitate_Residential_DER_Services"],
  ["Thurner, L. et al. (2018). pandapower — an open-source Python tool for convenient modeling, analysis, and optimization of electric power systems. IEEE Trans. Power Systems.", "https://pandapower.readthedocs.io"],
  ["Richardson, I. et al. (2010). Domestic electricity use: a high-resolution energy demand model. Energy and Buildings. (richardsonpy implementation, RWTH Aachen)", "https://github.com/RWTH-EBC/richardsonpy"],
  ["Ruhnau, O., Hirth, L. & Praktiknjo, A. (2019). Time series of heat demand and heat pump efficiency for energy system modeling. Scientific Data 6:189.", "https://github.com/oruhnau/when2heat"],
  ["Dobos, A. P. (2014). PVWatts Version 5 Manual. NREL/TP-6A20-62641.", "https://www.nrel.gov/docs/fy14osti/62641.pdf"],
  ["Persson, E. & Jonsson, P. (2018). Utvärdering av Velanders formel för toppeffektberäkning i eldistributionsnät. MDU / Mälarenergi Elnät.", "https://mdu.diva-portal.org/smash/get/diva2:1223815/FULLTEXT01.pdf"],
  ["Energimarknadsinspektionen, EIFS 2023:3: voltage quality requirements (guidance).", "https://ei.se/bransch/eloverforingens-kvalitet/spanningskvalitet-i-elnatet/spanningskvalitet---vagledning-eifs-20233"],
  ["Energiwirtschaftsgesetz (EnWG) §11 Abs. 2, Spitzenkappung.", "https://www.gesetze-im-internet.de/enwg_2005/__11.html"],
  ["IEA (2026). Electricity 2026: Grids.", "https://www.iea.org/reports/electricity-2026/grids"],
];

export default function Method() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  // text that comes with the data: the interface's words when it has them, the data's otherwise
  const tr = (key: string, fallback: string) => {
    const s = t(key);
    return s === key ? fallback : s;
  };
  const src = useApi<any>("/sources");
  const status = useApi<any>("/sources/status", { refetchMs: 5 * 60 * 1000 });
  const ass = useApi<any>("/assumptions");
  const val = useApi<any>("/validation");
  const meta = useApi<any>("/meta");
  const host = useApi<any>("/hosting");
  const fc = useApi<any>("/forecast");
  const prot = useApi<any>("/protection");
  const P = prot.data?.data;
  const FC = fc.data?.data;
  const [tag, setTag] = useState<string>("all");
  const V = val.data?.data;
  const F = host.data?.data?.foresight;
  // the envelopes re-issued during the day, replayed after the fact (intraday.json)
  const ID = host.data?.data?.intraday;
  // the MV band, a sensitivity beside the published envelopes (mv_band.json), and what the static
  // figure assumes (hosting_summary.json)
  const MV = host.data?.data?.mv_band;
  const SD = host.data?.data?.summary?.static_definition;

  const statusByKey = useMemo(() => {
    const m = new Map<string, any>();
    (status.data?.results ?? []).forEach((r: any) => m.set(r.key, r));
    return m;
  }, [status.data]);

  const cols: Col<Input>[] = useMemo(() => [
    { key: "label", label: t("tbl.input"), value: (a) => localized(a, "label", lang) },
    {
      key: "value",
      label: t("tbl.value"),
      numeric: true,
      value: (a) => (typeof a.value === "number" ? a.value : Array.isArray(a.value) ? a.value.join(", ") : String(a.value)),
      cell: (a) => (
        <span className="nowrap">
          {Array.isArray(a.value) ? a.value.join(", ") : String(a.value)} {a.unit}
        </span>
      ),
    },
    { key: "tag", label: t("tbl.tag"), value: (a) => t(`met.tag.${a.tag}`), cell: (a) => <span className="nowrap">{t(`met.tag.${a.tag}`)}</span> },
    {
      key: "source",
      label: t("tbl.source"),
      value: (a) => a.source,
      cell: (a) => (
        <>
          {a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.source}</a> : a.source}
          {a.note ? <div className="caption-muted">{localized(a, "note", lang)}</div> : null}
        </>
      ),
    },
  ], [lang]);

  if (src.isLoading || ass.isLoading) return <PageSkeleton bar blocks={2} />;
  if (src.isError || ass.isError) return <Missing of={[src, ass, val, meta]} />;

  const inputs: Input[] = ass.data?.assumptions ?? [];
  const shown = inputs.filter((a) => tag === "all" || a.tag === tag);
  const counts = TAGS.map((tg) => inputs.filter((a) => a.tag === tg).length);
  // everything that is not Kapacitet+'s own modelling choice, however the tags are distributed
  const ours = counts[TAGS.indexOf("team-assumption")];

  const sections: Section[] = [
    { id: "how", label: t("met.how") },
    { id: "sources", label: t("met.sources") },
    { id: "inputs", label: t("met.assumptions") },
    ...(V || val.isLoading ? [{ id: "validation", label: t("met.validation") }] : []),
    ...(F || host.isLoading ? [{ id: "forecast", label: t("met.nav.fc") }] : []),
    ...(MV ? [{ id: "mvband", label: t("met.nav.mv") }] : []),
    ...(P ? [{ id: "protection", label: t("met.nav.prot") }] : []),
    { id: "import", label: t("met.nav.imp") },
    { id: "limits", label: t("met.limits") },
  ];

  return (
    <div className="page">
      <SectionBar
        sections={sections}
        basis={status.data?.checked_at
          ? <span>{t("met.status")}: {clock(lang, status.data.checked_at)}</span>
          : undefined}
      />
      <div className="page-inner">
        {/* ---------------------------------------------------------------- the claim */}
        <section className="region">
          <h1 className="headline">{t("met.title")}</h1>
          <p className="headline-sub">{t("met.sub")}</p>
          <p className="caption">{t("met.inputsLine", { n: f(inputs.length), rest: inputs.length - ours, team: ours })}</p>
          {meta.data?.run?.computed_at && (
            <p className="caption-muted">
              {t(meta.data.run.quick ? "met.runQuick" : "met.run", {
                when: dateTime(lang, meta.data.run.computed_at, false),
                v: meta.data.run.versions?.kapacitet ?? "–",
              })}
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------------- how it works */}
        <section className="region" id="how">
          <div className="region-head">
            <div className="region-title"><h2>{t("met.how")}</h2></div>
          </div>
          <HowItWorks
            houses={meta.data?.grid?.customers ?? "–"}
            pool={V?.households?.n_pool ?? "–"}
            years={meta.data?.weather_years?.length ?? "–"}
          />
        </section>

        {/* ---------------------------------------------------------------- sources */}
        <section className="region" id="sources">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("met.sources")}</h2>
              <p className="caption">{t("met.sources.caption")}</p>
            </div>
          </div>
          <div className="well">
            <div className="src-list">
              {src.data?.sources.map((s: any) => {
                const st = statusByKey.get(s.key);
                return (
                  <div key={s.key} className="src">
                    <div>
                      <span className="src-name">{tr(`src.${s.key}.name`, s.name)}</span> <Chip kind={s.tag} />
                    </div>
                    <div>
                      {st ? (st.ok ? <Status kind="good">{t("met.online")}, {st.ms} ms</Status> : <Status kind="serious">{t("met.offline")}</Status>) : null}
                    </div>
                    <div className="src-meta">
                      {s.provider}. {tr(`src.${s.key}.use`, s.used_for)}. <em>{s.license}</em>.{" "}
                      {s.files.length > 0 && (s.files.length === 1 ? t("met.file") : t("met.files", { n: s.files.length }))}
                      {s.files.length > 0 && `, ${s.files[0].fetched_at.slice(0, 10)}`}
                      {s.homepage && <> <a href={s.homepage} target="_blank" rel="noreferrer">{s.homepage.replace(/^https?:\/\//, "").split("/")[0]}</a></>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- model inputs */}
        <section className="region" id="inputs">
          <div className="region-head">
            <div className="region-title"><h2>{t("met.assumptions")}</h2></div>
            <div className="seg" role="group" aria-label={t("met.filterTag")}>
              <button aria-pressed={tag === "all"} onClick={() => setTag("all")}>{t("met.all")}</button>
              {TAGS.map((tg, i) => counts[i] > 0 && (
                <button key={tg} aria-pressed={tag === tg} onClick={() => setTag(tg)}>{t(`met.tag.${tg}`)} ({counts[i]})</button>
              ))}
            </div>
          </div>
          {/* the long table of the screen: searchable, sortable, on its own ground */}
          <div className="well">
            <DataTable
              rows={shown}
              cols={cols}
              rowKey={(a) => a.key}
              caption={t("met.inputs.caption")}
              searchLabel={t("met.inputs.search")}
            />
          </div>
        </section>

        {/* ---------------------------------------------------------------- validation */}
        {!V && <SectionMissing title={t("met.validation")} q={val} />}
        {!V && val.isLoading && (
          <section className="region" id="validation" aria-busy="true">
            <div className="region-head"><div className="region-title"><h2>{t("met.validation")}</h2></div></div>
            <ChartSkeleton height={220} />
          </section>
        )}
        {V && (
          <section className="region" id="validation">
            <div className="region-head">
              <div className="region-title"><h2>{t("met.validation")}</h2></div>
            </div>

            <div className="stack">
              <h3>{t("met.pf")}</h3>
              <div className="tiles cols-4">
                <Tile label={t("met.pf.dv")} value={V.powerflow.max_abs_dv_pu.toExponential(1)} unit="pu" note={t("met.pf.cases", { n: V.powerflow.cases })} />
                <Tile label={t("met.pf.dl")} value={V.powerflow.max_abs_dloading_pct.toExponential(1)} unit="%" />
                <Tile label={t("met.pf.speed")} value={f(V.powerflow.zbus_seconds_per_year, 1)} unit="s" note={t("met.pf.size", { b: V.powerflow.buses, l: V.powerflow.lines })} />
                <Tile label={t("met.pf.nr")} value={f(V.powerflow.pandapower_ms_per_case, 0)} unit="ms" />
              </div>
            </div>

            {/* the two input checks are one instrument, so they share one panel */}
            <section className="block">
              <div className="grid-2">
                <div className="stack">
                  <h3>{t("met.strang")}</h3>
                  <p className="caption">{t("met.strang.caption")}</p>
                  <Legend items={[{ color: "var(--series-main)", label: t("met.strang.morning"), line: true }, { color: "var(--cat-1)", label: t("met.strang.afternoon"), line: true }]} />
                  <ChartOrTable
                    chart={<Chart ariaLabel={t("met.strang")} height={220} deps={[V]} build={(tk, _w, fm) => strangOption(tk, V.strang_timestamp.shifts, fm)} />}
                    table={
                      <table className="data">
                        <thead><tr><th className="r">{t("tbl.shiftMin")}</th><th className="r">{t("tbl.morning")}</th><th className="r">{t("tbl.afternoon")}</th></tr></thead>
                        <tbody>{(V.strang_timestamp.shifts as any[]).map((s) => <tr key={s.shift_min}><td className="r">{f(s.shift_min)}</td><td className="r">{f(s.morning_k, 4)}</td><td className="r">{f(s.afternoon_k, 4)}</td></tr>)}</tbody>
                      </table>
                    }
                  />
                </div>
                {V.household_shape && (
                  <div className="stack">
                    <h3>{t("met.shape")}</h3>
                    <p className="caption">{t("met.shape.caption", { year: V.household_shape.year, r: f(V.household_shape.corr_hour_of_day, 2) })}</p>
                    <Legend items={[{ color: "var(--series-main)", label: t("met.shape.sim"), line: true }, { color: "var(--cat-1)", label: t("met.shape.real"), line: true }]} />
                    <ChartOrTable
                      chart={<Chart ariaLabel={t("met.shape")} height={220} deps={[V]} build={(tk, _w, fm) => shapeOption(tk, V.household_shape, t, fm)} />}
                      table={
                        <table className="data">
                          <thead><tr><th>{t("tbl.hour")}</th><th className="r">{t("tbl.simulated")}</th><th className="r">{t("tbl.measured")}</th></tr></thead>
                          <tbody>{(V.household_shape.hour_of_day_sim as number[]).map((v, h) => <tr key={h}><td>{String(h).padStart(2, "0")}:00</td><td className="r">{f(v, 2)}</td><td className="r">{f(V.household_shape.hour_of_day_real[h], 2)}</td></tr>)}</tbody>
                        </table>
                      }
                    />
                  </div>
                )}
              </div>
            </section>

            <div className="stack">
              <h3>{t("met.weather")}</h3>
              <ChartOrTable
                chart={<Chart ariaLabel={t("met.weather")} height={200} deps={[V]} build={(tk, _w, fm) => weatherOption(tk, V.weather, fm)} />}
                table={
                  <table className="data">
                    <thead><tr><th>{t("tbl.year")}</th><th className="r">{t("tbl.ghi")}, kWh/m²</th><th className="r">{t("tbl.tmean")}</th><th className="r">{t("tbl.tmin")}</th><th className="r">{t("tbl.tmax")}</th><th className="r">{t("tbl.filled")}</th></tr></thead>
                    <tbody>{V.weather.map((w: any) => (
                      <tr key={w.year}><td>{w.year}</td><td className="r">{f(w.ghi_kwh_m2)}</td><td className="r">{f(w.t_mean_c, 1)}</td><td className="r">{f(w.t_min_c, 1)}</td><td className="r">{f(w.t_max_c, 1)}</td><td className="r">{w.t_filled_hours}</td></tr>
                    ))}</tbody>
                  </table>
                }
              />
              <div className="tiles cols-4">
                <Tile label={t("met.diffuse")} value={f(V.diffuse_closure?.mean_abs_w_m2, 2)} unit="W/m²" note={t("met.diffuse.note", { year: V.diffuse_closure?.year ?? "" })} />
                <Tile label={t("met.pvYield")} value={f(V.pv_yield_kwh_per_kwp.median)} unit="kWh/kWp" note={`${V.pv_yield_kwh_per_kwp.year}`} />
                <Tile label={t("met.hhEnergy")} value={`${f(V.households.p10_kwh)}–${f(V.households.p90_kwh)}`} unit="kWh" note={t("met.hhEnergy.note", { raw: f(V.households.crest_raw_mean_kwh), k: f(V.households.scale_factor, 2) })} />
                <Tile label={t("met.hhPeak")} value={`${f(V.households.peak_10min_kw_median, 1)} / ${f(V.households.peak_hourly_kw_median, 1)}`} unit="kW" note={t("met.hhPeak.note")} />
              </div>
            </div>
          </section>
        )}

        {/* ---------------------------------------------------------------- the cost of foresight */}
        {!F && <SectionMissing title={t("met.fc")} q={host} />}
        {!F && host.isLoading && (
          <section className="region" id="forecast" aria-busy="true">
            <div className="region-head"><div className="region-title"><h2>{t("met.fc")}</h2></div></div>
            <ChartSkeleton height={120} />
          </section>
        )}
        {F && (
          <section className="region" id="forecast">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("met.fc")}</h2>
                <p className="caption">
                  {t(F.margins ? "met.fc.captionMargins" : "met.fc.caption", { years: F.years.length - 1, pv: f(F.margins?.pv_pct ?? 0), load: f(F.margins?.load_pct ?? 0) })}
                </p>
              </div>
            </div>
            <div className="tiles cols-4">
              <Tile label={t("met.fc.static")} value={f(F.median_kw.static, 0)} unit="kW" />
              {F.median_kw.timeseries != null && <Tile label={t("met.fc.timeseries")} value={f(F.median_kw.timeseries, 0)} unit="kW" />}
              <Tile label={t("met.fc.climo")} value={f(F.median_kw.climatological, 0)} unit="kW" />
              <Tile label={t("met.fc.doe")} value={f(F.median_kw.doe, 0)} unit="kW" />
            </div>
            <div className="tiles">
              <Tile label={t("met.fc.premium")} value={f(F.foresight_premium_kw, 0)} unit="kW" />
              <Tile label={t("met.fc.violations", { n: F.n_orders })} value={f(F.violation_hours)} unit="h" />
              {F.within_budget_orders != null && (
                <Tile label={t("met.fc.withinBudget", { z: f(F.z_star_pct) })} value={`${f(F.within_budget_orders)} / ${f(F.n_orders)}`}
                  note={F.at_floor_orders != null ? t("met.fc.withinBudget.note", { n: f(F.at_floor_orders) }) : undefined} />
              )}
              <Tile
                label={F.exceedance.year != null ? t("met.fc.record", { year: F.exceedance.year }) : t("met.fc.record.noYear")}
                value={f(F.exceedance.pv_aggregate_hours_over)}
                unit="h"
                note={F.plan_exceedance?.total
                  ? t("met.fc.record.noteAll", { n: f(F.years.length), h: f(F.plan_exceedance.total.pv_aggregate_hours_over), kw: f(F.plan_exceedance.total.pv_worst_aggregate_kw, 2) })
                  : t("met.fc.record.note", { r: f(F.exceedance.pv_worst_aggregate_ratio, 3) })}
              />
            </div>
            {ID && <Intraday ID={ID} F={F} />}
            <p className="caption-muted">{t("met.fc.bound")}</p>
            {SD && (
              <section className="block stack">
                <div className="block-head"><h3>{t("met.static.title")}</h3></div>
                <p className="caption">
                  {SD.load_rule !== "min_daytime_summer" ? SD.load_rule_text : t("met.static.text", {
                    pv: f(SD.pv_factor, 2), load: f(SD.load_factor, 2), mv: f(SD.mv_voltage_pu, 2),
                    from: monthLong(lang, SD.months[0]), to: monthLong(lang, SD.months[1]), h0: SD.hours[0], h1: SD.hours[1],
                  })}
                </p>
              </section>
            )}
            <SectionMissing title={t("met.fc.schemes")} q={fc} />
            {FC && (
              <section className="block stack">
                <div className="block-head"><h3>{t("met.fc.schemes")}</h3></div>
                <p className="caption">{t("met.fc.schemesCaption", { year: FC.year, days: FC.window_days })}</p>
                <Protocol
                  rows={(FC.rows as any[]).map((r) => ({
                    ok: r.safe,
                    label: t(`fc.scheme.${r.scheme}`),
                    value: f(r.curtailed_share_pct, 1),
                    unit: "%",
                    criterion: t("met.fc.schemeLine", { v: f(r.violation_hours), h: f(r.hours_limited) }),
                  }))}
                />
              </section>
            )}
          </section>
        )}

        {/* ---------------------------------------------------------------- the MV band */}
        {MV && <MvBand MV={MV} />}

        {/* ---------------------------------------------------------------- protection */}
        <SectionMissing title={t("met.prot")} q={prot} />
        {P && (
          <section className="region" id="protection">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("met.prot")}</h2>
                <p className="caption">{t("met.prot.caption", { s: f(P.criterion.disconnect_s) })}</p>
              </div>
            </div>
            <p className="lead-answer">{t("met.prot.summary", {
              ok: f(P.summary.feeders_ok), n: f(P.summary.feeders), s: f(P.criterion.disconnect_s),
              hok: f(P.summary.houses_ok), hn: f(P.summary.houses),
            })}</p>
            <Protocol
              rows={[
                ...(P.feeders as any[]).map((r) => ({
                  ok: r.ok,
                  label: t("met.prot.feeder", { n: r.feeder + 1 }),
                  value: f(r.ik1_min_a),
                  unit: "A",
                  criterion: r.fuse_a == null ? t("met.prot.noFuse", { cable: r.cable, iz: f(r.i_z_a) }) : t("met.prot.line", {
                    fuse: f(r.fuse_a), cable: r.cable, iz: f(r.i_z_a), s: f(P.criterion.disconnect_s), gate: f(r.gate_5s_a),
                    margin: f(r.margin, 2),
                  }),
                })),
                {
                  ok: P.summary.houses_ok === P.summary.houses,
                  label: t("met.prot.houses"),
                  value: f(P.summary.ik1_min_a),
                  unit: "A",
                  criterion: t("met.prot.housesLine", {
                    ok: f(P.summary.houses_ok), n: f(P.summary.houses), s: f(P.criterion.disconnect_s),
                    median: f(P.summary.ik1_median_a),
                  }),
                },
              ]}
            />
            <p className="caption-muted">{t("met.prot.missing")}</p>
          </section>
        )}

        {/* ---------------------------------------------------------------- bring your own network */}
        <section className="region" id="import">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("met.imp")}</h2>
              <p className="caption">{t("met.imp.caption")}</p>
            </div>
          </div>
          <NetworkCheck />
          <NetworksPanel />
        </section>

        {/* ---------------------------------------------------------------- limits & refs */}
        <section className="region" id="limits">
          <div className="region-head">
            <div className="region-title"><h2>{t("met.limits")}</h2></div>
          </div>
          <div className="grid-2">
            <ul className="list caption">
              {Array.from({ length: N_LIMITS }, (_, i) => <li key={i}>{t(`met.limit.${i + 1}`)}</li>)}
            </ul>
            <section className="block">
              <div className="block-head"><h3>{t("met.refs")}</h3></div>
              <ol className="list caption">
                {REFS.map(([r, u]) => <li key={r}>{u ? <a href={u} target="_blank" rel="noreferrer">{r}</a> : r}</li>)}
              </ol>
            </section>
          </div>
        </section>
      </div>
    </div>
  );
}

function HowItWorks({ houses, pool, years }: { houses: number | string; pool: number | string; years: number | string }) {
  const t = useT();
  const steps = [1, 2, 3, 4, 5].map((i) => t(`met.step.${i}`, { houses, pool, years }));
  return (
    <ol className="list">
      {steps.map((s) => <li key={s}>{s}</li>)}
    </ol>
  );
}

function strangOption(tk: Tokens, shifts: any[], f: (v: number | null | undefined, d?: number) => string) {
  return {
    grid: { left: 44, right: 12, top: 12, bottom: 34 },
    xAxis: { type: "value", ...axisStyle(tk), name: "min", nameLocation: "middle", nameGap: 24 },
    yAxis: { type: "value", ...axisStyle(tk), scale: true },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => f(v, 4) },
    series: [
      { type: "line", data: shifts.map((s) => [s.shift_min, round(s.morning_k, 4)]), lineStyle: { width: 2, color: tk.main }, itemStyle: { color: tk.main, borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
      { type: "line", data: shifts.map((s) => [s.shift_min, round(s.afternoon_k, 4)]), lineStyle: { width: 2, color: tk.cat[0] }, itemStyle: { color: tk.cat[0], borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
    ],
  };
}

function shapeOption(tk: Tokens, sh: any, t: (k: string) => string, f: (v: number | null | undefined, d?: number) => string) {
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}`);
  return {
    grid: { left: 40, right: 12, top: 12, bottom: 28 },
    xAxis: { type: "category", data: hours, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 3 } },
    yAxis: { type: "value", ...axisStyle(tk), scale: true, name: t("met.shape.axis") },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => f(v, 2) },
    series: [
      { type: "line", data: sh.hour_of_day_sim, lineStyle: { width: 2, color: tk.main }, symbol: "none" },
      { type: "line", data: sh.hour_of_day_real, lineStyle: { width: 2, color: tk.cat[0] }, symbol: "none" },
    ],
  };
}

function weatherOption(tk: Tokens, w: any[], f: (v: number | null | undefined, d?: number) => string) {
  return {
    grid: { left: 48, right: 12, top: 12, bottom: 26 },
    xAxis: { type: "category", data: w.map((x) => String(x.year)), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), name: "kWh/m²", min: 0 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${f(v)} kWh/m²` },
    series: [{ type: "bar", data: w.map((x) => Math.round(x.ghi_kwh_m2)), itemStyle: { color: tk.main, borderRadius: [tk.size.radius, tk.size.radius, 0, 0] }, barWidth: 18 }],
  };
}

/* ==================================================================== the MV band */

/**
 * The station's MV voltage across its band (mv_band.json): the capacity search rerun at each level
 * and the published envelopes replayed at it. A sensitivity: the envelopes stay at the base level.
 */
function MvBand({ MV }: { MV: any }) {
  const t = useT();
  const f = useFmt();
  const levels = MV.levels as any[];
  const safe = levels.filter((l) => l.published.violation_hours === 0).map((l) => l.vm_pu as number);
  return (
    <section className="region" id="mvband">
      <div className="region-head">
        <div className="region-title">
          <h2>{t("met.mv")}</h2>
          <p className="caption">
            {t("met.mv.caption", { lo: f(MV.band_pu[0], 2), hi: f(MV.band_pu[1], 2), base: f(MV.base_pu, 2), order: MV.order, year: MV.year })}
          </p>
        </div>
      </div>
      {safe.length > 0 && (
        <p className="lead-answer">
          {t(safe.length === levels.length ? "met.mv.leadAll" : "met.mv.lead", { from: f(Math.min(...safe), 2), to: f(Math.max(...safe), 2) })}
        </p>
      )}
      <table className="data">
        <caption className="sr-only">{t("met.mv.table")}</caption>
        <thead>
          <tr>
            <th>{t("met.mv.col.pu")}</th>
            <th className="r">{t("met.mv.col.static")}</th>
            <th className="r">{t("met.mv.col.ts")}</th>
            <th className="r">{t("met.mv.col.doe")}</th>
            <th className="r">{t("met.mv.col.hours")}</th>
            <th className="r">{t("met.mv.col.trafo")}</th>
            <th className="r">{t("met.mv.col.vmax")}</th>
          </tr>
        </thead>
        <tbody>
          {levels.map((l) => (
            <tr key={l.vm_pu} aria-current={l.vm_pu === MV.base_pu ? "true" : undefined}>
              {/* the row's key, flush left so its digits align and the published level's word follows them */}
              <td className="num">{f(l.vm_pu, 2)}{l.vm_pu === MV.base_pu && <span className="row-mark">{t("met.mv.base")}</span>}</td>
              <td className="r">{f(l.static_kw)}</td>
              <td className="r">{f(l.timeseries_kw)}</td>
              <td className="r">{f(l.doe_kw)}</td>
              <td className={`r${l.published.violation_hours > 0 ? " danger" : ""}`}>{f(l.published.violation_hours)}</td>
              <td className="r">{f(l.published.max_trafo_loading_pct, 1)}</td>
              <td className="r">{f(l.published.max_voltage_pu, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="caption-muted">{t("met.mv.note")}</p>
    </section>
  );
}

/** The intraday stop between the day-ahead plan and perfect foresight, and the replica's scorecard. */
function Intraday({ ID, F }: { ID: any; F: any }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  // both budget bases, each named: M10 keeps the aggregate one as the headline
  const card = (b: any) => {
    const sc = b.scorecard;
    const gain = sc.gain_over_hourly_pct;
    const agr = sc.agreements_within_pct;
    return [
      {
        ok: sc.violation_hours.result === "pass",
        label: t("met.id.sc.violations", { n: f(ID.n_orders) }),
        value: f(sc.violation_hours.value),
        unit: "h",
        criterion: t("met.id.sc.violations.crit", { max: f(sc.violation_hours.threshold) }),
      },
      {
        ok: agr.result !== "fail",
        label: t("met.id.sc.agreements", { z: f(ID.z_star_pct) }),
        value: f(agr.value, 1),
        unit: "%",
        criterion: t(agr.result === "pass_if_justified" ? "met.id.sc.agreements.justified" : "met.id.sc.agreements.crit",
          { min: f(agr.threshold), floor: f(agr.justified_threshold) }),
      },
      {
        ok: gain.result === "pass",
        label: t("met.id.sc.gain"),
        value: gain.value == null ? "–" : f(gain.value, 1),
        unit: "%",
        criterion: t("met.id.sc.gain.crit", { min: f(gain.threshold), kw: f(gain.hourly_kw, 0) }),
      },
      {
        ok: null,
        label: t("met.id.sc.unnecessary"),
        value: f(b.unnecessary_hours),
        unit: "h",
        criterion: t("met.id.sc.unnecessary.crit", { h: f(b.hours_limited), n: f(ID.n_orders), y: f(ID.years.length) }),
      },
      ...(b.plan_exceedance ? [{
        ok: null,
        label: t("met.id.sc.past"),
        value: f(b.plan_exceedance.net_aggregate_hours_not_dominated),
        unit: "h",
        criterion: t("met.id.sc.past.crit", { pv: f(b.plan_exceedance.pv_aggregate_hours_over), kw: f(b.plan_exceedance.net_worst_aggregate_kw, 2) }),
      }] : []),
    ];
  };
  return (
    <section className="block stack" id="intraday">
      <div className="block-head"><h3>{t("met.id.title")}</h3></div>
      <p className="caption">
        {t("met.id.caption", { gates: listJoin(lang, (ID.gates_h as number[]).map((g) => f(g))), lag: f(ID.measurement_lag_h), years: f(ID.years.length - 1) })}
      </p>
      <div className="tiles cols-3">
        <Tile label={t("met.fc.climo")} value={f(F.median_kw.climatological, 0)} unit="kW" />
        <Tile label={t("met.id.kw")} value={f(ID.median_kw, 0)} unit="kW"
          note={t("met.id.kw.note", { lo: f(ID.kw_ac_stats.p10, 0), hi: f(ID.kw_ac_stats.p90, 0), inst: f(ID.installation.median_kw, 0) })} />
        <Tile label={t("met.fc.doe")} value={f(F.median_kw.doe, 0)} unit="kW" />
      </div>
      {(["aggregate", "installation"] as const).map((basis) => (
        <div className="stack" key={basis}>
          <h4>{t(`cmp.stop.basis.${basis}`, { z: f(ID.z_star_pct) })}</h4>
          <Protocol rows={card(basis === "aggregate" ? ID : ID.installation)} />
        </div>
      ))}
    </section>
  );
}
