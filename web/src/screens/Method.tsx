import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { PageSkeleton } from "../components/Skeleton";
import { SectionBar, type Section } from "../components/SectionBar";
import { DataTable, type Col } from "../components/DataTable";
import { ChartOrTable, Chip, Hero, Legend, Missing, Status, Tile } from "../components/bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

/** One entry of config/assumptions.toml, as /assumptions serves it. */
interface Input {
  key: string;
  label: string;
  value: number | string | (number | string)[];
  unit?: string;
  tag: string;
  source: string;
  url?: string;
  note?: string;
}

const TAGS = ["sourced", "regulation", "library", "derived", "team-assumption"];

const LIMITS: Record<"en" | "es", string[]> = {
  en: [
    "The published envelopes use perfect foresight of each household's load and solar production. What that is worth is measured above rather than left as a caveat: the same search against a day-ahead state that uses no forecast at all carries less capacity, and the two bracket a real operator.",
    "Hourly resolution: the Swedish limit (Ei EIFS 2023:3) applies to 10-minute means. A 1 % voltage margin covers the difference; the 1-minute household data shows how much higher 10-minute peaks are.",
    "Balanced three-phase model. Single-phase inverters and chargers create unbalance that is not modelled here.",
    "The LV network is synthetic: real building and street geometry, but cable routes, cabinets, cable types and the transformer size follow design rules, not a DSO's records.",
    "Household demand comes from the CREST model (UK behaviour data) scaled to Swedish annual consumption; heat-pump demand uses national statistics for pre-1940 houses.",
    "The medium-voltage supply is held at 1.00 pu; its variation is explored in the sensitivity chart.",
    "Economics use the SE3 spot price only: grid fees, energy tax and any compensation for curtailment are not included.",
    "The simulated households have less night-time base load and a sharper evening peak than eSett's measured Stockholm profile (see the validation chart); around midday, when curtailment happens, the two agree closely.",
    "Protection is not checked yet: minimum fault currents at the feeder ends against fuse ratings are a next step.",
  ],
  es: [
    "Las envolventes publicadas usan previsión perfecta del consumo y la producción solar de cada vivienda. Lo que eso vale se mide arriba en vez de quedar como advertencia: la misma búsqueda contra un estado del día anterior que no usa ningún pronóstico sostiene menos capacidad, y las dos acotan a un operador real.",
    "Resolución horaria: el límite sueco (Ei EIFS 2023:3) se aplica a medias de 10 minutos. Un margen de tensión del 1 % cubre la diferencia; los datos de 1 minuto de los hogares muestran cuánto más altos son los picos de 10 minutos.",
    "Modelo trifásico equilibrado. Los inversores y cargadores monofásicos crean desequilibrio que aquí no se modela.",
    "La red de BT es sintética: geometría real de edificios y calles, pero el trazado de cables, los armarios, los tipos de cable y la potencia del transformador siguen reglas de diseño, no los registros de una distribuidora.",
    "El consumo de los hogares viene del modelo CREST (datos de comportamiento del Reino Unido) escalado al consumo anual sueco; las bombas de calor usan estadísticas nacionales de viviendas anteriores a 1940.",
    "La tensión de media tensión se mantiene en 1,00 pu; su variación se explora en el gráfico de sensibilidad.",
    "La economía usa solo el precio spot SE3: no incluye tarifas de red, impuesto sobre la energía ni compensación por recorte.",
    "Los hogares simulados tienen menos consumo base nocturno y un pico vespertino más marcado que el perfil medido de Estocolmo de eSett (ver el gráfico de validación); hacia el mediodía, cuando ocurre el recorte, ambos coinciden bien.",
    "Aún no se verifican las protecciones: las corrientes mínimas de cortocircuito al final de los alimentadores frente a los fusibles son un paso siguiente.",
  ],
};

const REFS = [
  ["Bollen, M. & Hassan, F. (2011). Integration of Distributed Generation in the Power System. Wiley–IEEE Press.", ""],
  ["Petrou, K. et al. (2020). Operating envelopes for prosumers in LV networks: a weighted proportional fairness approach. IEEE PES ISGT-Europe.", ""],
  ["Liu, M. Z. et al. (2022). Using OPF-based operating envelopes to facilitate residential DER services. IEEE Trans. Smart Grid.", "https://www.researchgate.net/publication/361412635_Using_OPF-Based_Operating_Envelopes_to_Facilitate_Residential_DER_Services"],
  ["Thurner, L. et al. (2018). pandapower — an open-source Python tool for convenient modeling, analysis, and optimization of electric power systems. IEEE Trans. Power Systems.", "https://pandapower.readthedocs.io"],
  ["Richardson, I. et al. (2010). Domestic electricity use: a high-resolution energy demand model. Energy and Buildings. (richardsonpy implementation, RWTH Aachen)", "https://github.com/RWTH-EBC/richardsonpy"],
  ["Ruhnau, O., Hirth, L. & Praktiknjo, A. (2019). Time series of heat demand and heat pump efficiency for energy system modeling. Scientific Data 6:189.", "https://github.com/oruhnau/when2heat"],
  ["Dobos, A. P. (2014). PVWatts Version 5 Manual. NREL/TP-6A20-62641.", "https://www.nrel.gov/docs/fy14osti/62641.pdf"],
  ["Persson, E. & Jonsson, P. (2018). Utvärdering av Velanders formel för toppeffektberäkning i eldistributionsnät. MDU / Mälarenergi Elnät.", "https://mdu.diva-portal.org/smash/get/diva2:1223815/FULLTEXT01.pdf"],
  ["Energimarknadsinspektionen, EIFS 2023:3 — voltage quality requirements (guidance).", "https://ei.se/bransch/eloverforingens-kvalitet/spanningskvalitet-i-elnatet/spanningskvalitet---vagledning-eifs-20233"],
  ["Energiwirtschaftsgesetz (EnWG) §11 Abs. 2 — Spitzenkappung.", "https://www.gesetze-im-internet.de/enwg_2005/__11.html"],
  ["IEA (2026). Electricity 2026: Grids.", "https://www.iea.org/reports/electricity-2026/grids"],
];

export default function Method() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const src = useApi<any>("/sources");
  const status = useApi<any>("/sources/status", { refetchMs: 5 * 60 * 1000 });
  const ass = useApi<any>("/assumptions");
  const val = useApi<any>("/validation");
  const meta = useApi<any>("/meta");
  const host = useApi<any>("/hosting");
  const [tag, setTag] = useState<string>("all");
  const V = val.data?.data;
  const F = host.data?.data?.foresight;

  const statusByKey = useMemo(() => {
    const m = new Map<string, any>();
    (status.data?.results ?? []).forEach((r: any) => m.set(r.key, r));
    return m;
  }, [status.data]);

  const cols: Col<Input>[] = useMemo(() => [
    { key: "label", label: t("tbl.input"), value: (a) => a.label },
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
          {a.note ? <div className="caption-muted">{a.note}</div> : null}
        </>
      ),
    },
  ], [lang]);

  if (src.isLoading || ass.isLoading) return <PageSkeleton blocks={3} />;
  if (src.isError || ass.isError) return <Missing of={[src, ass, val, meta]} />;

  const inputs: Input[] = ass.data?.assumptions ?? [];
  const shown = inputs.filter((a) => tag === "all" || a.tag === tag);
  const counts = TAGS.map((tg) => inputs.filter((a) => a.tag === tg).length);
  // everything that is not the team's own judgement, however the tags are distributed
  const ours = counts[TAGS.indexOf("team-assumption")];

  const sections: Section[] = [
    { id: "how", label: t("met.how") },
    { id: "sources", label: t("met.sources") },
    { id: "inputs", label: t("met.assumptions") },
    ...(V ? [{ id: "validation", label: t("met.validation") }] : []),
    ...(F ? [{ id: "forecast", label: t("met.nav.fc") }] : []),
    { id: "limits", label: t("met.limits") },
  ];

  return (
    <div className="page">
      <SectionBar
        sections={sections}
        basis={status.data?.checked_at
          ? <span>{t("met.status")}: {new Date(status.data.checked_at).toLocaleTimeString(lang === "es" ? "es-ES" : "en-GB")}</span>
          : undefined}
      />
      <div className="page-inner">
        {/* ---------------------------------------------------------------- the claim */}
        <section className="region">
          <h1 className="headline">{t("met.title")}</h1>
          <p className="headline-sub">{t("met.sub")}</p>
          <Hero
            value={f(inputs.length)}
            label={t("met.hero.label")}
            note={t("met.hero.note", { rest: inputs.length - ours, team: ours })}
          />
        </section>

        {/* ---------------------------------------------------------------- how it works */}
        <section className="region" id="how">
          <div className="region-head">
            <div className="region-title"><h2>{t("met.how")}</h2></div>
          </div>
          <HowItWorks
            lang={lang}
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
                      <span className="src-name">{s.name}</span> <Chip kind={s.tag} />
                    </div>
                    <div>
                      {st ? (st.ok ? <Status kind="good">{t("met.online")}, {st.ms} ms</Status> : <Status kind="serious">{t("met.offline")}</Status>) : null}
                    </div>
                    <div className="src-meta">
                      {s.provider}. {s.used_for}. <em>{s.license}</em>.{" "}
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
            <div className="seg">
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
                  <Chart ariaLabel={t("met.strang")} height={220} deps={[V]} build={(tk) => strangOption(tk, V.strang_timestamp.shifts)} />
                </div>
                {V.household_shape && (
                  <div className="stack">
                    <h3>{t("met.shape")}</h3>
                    <p className="caption">{t("met.shape.caption", { year: V.household_shape.year, r: f(V.household_shape.corr_hour_of_day, 2) })}</p>
                    <Legend items={[{ color: "var(--series-main)", label: t("met.shape.sim"), line: true }, { color: "var(--cat-1)", label: t("met.shape.real"), line: true }]} />
                    <Chart ariaLabel={t("met.shape")} height={220} deps={[V]} build={(tk) => shapeOption(tk, V.household_shape)} />
                  </div>
                )}
              </div>
            </section>

            <div className="stack">
              <h3>{t("met.weather")}</h3>
              <ChartOrTable
                chart={<Chart ariaLabel={t("met.weather")} height={200} deps={[V]} build={(tk) => weatherOption(tk, V.weather)} />}
                table={
                  <table className="data">
                    <thead><tr><th>{t("tbl.year")}</th><th className="r">GHI kWh/m²</th><th className="r">{t("tbl.tmean")}</th><th className="r">{t("tbl.tmin")}</th><th className="r">{t("tbl.tmax")}</th><th className="r">{t("tbl.filled")}</th></tr></thead>
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
        {F && (
          <section className="region" id="forecast">
            <div className="region-head">
              <div className="region-title">
                <h2>{t("met.fc")}</h2>
                <p className="caption">{t("met.fc.caption", { years: F.years.length - 1 })}</p>
              </div>
            </div>
            <div className="tiles cols-4">
              <Tile label={t("met.fc.static")} value={f(F.median_kw.static, 0)} unit="kW" />
              <Tile label={t("met.fc.climo")} value={f(F.median_kw.climatological, 0)} unit="kW" />
              <Tile label={t("met.fc.doe")} value={f(F.median_kw.doe, 0)} unit="kW" />
              <Tile label={t("met.fc.premium")} value={f(F.foresight_premium_kw, 0)} unit="kW" />
            </div>
            <div className="tiles">
              <Tile label={t("met.fc.violations", { n: F.n_orders })} value={F.violation_hours} />
              <Tile
                label={t("met.fc.record")}
                value={F.exceedance.pv_aggregate_hours_over}
                note={t("met.fc.record.note", { r: f(F.exceedance.pv_worst_aggregate_ratio, 3) })}
              />
            </div>
          </section>
        )}

        {/* ---------------------------------------------------------------- limits & refs */}
        <section className="region" id="limits">
          <div className="region-head">
            <div className="region-title"><h2>{t("met.limits")}</h2></div>
          </div>
          <div className="grid-2">
            <ul className="list caption">
              {LIMITS[lang].map((l) => <li key={l}>{l}</li>)}
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

function HowItWorks({ lang, houses, pool, years }: { lang: string; houses: number | string; pool: number | string; years: number | string }) {
  const steps = lang === "es"
    ? ["Ingesta: SMHI, eSett, precios SE3, SCB y OpenStreetMap, con registro de procedencia y hash de cada archivo.",
       `Red: ${houses} viviendas reales conectadas a la subestación mapeada; alimentadores radiales por las calles, armarios de cables y cables dimensionados con la fórmula de Velander.`,
       `Perfiles: ${pool} hogares simulados (CREST) con la distribución de tamaños de SCB, FV con pvlib y la irradiancia real, bombas de calor con la temperatura real y cargas de VE.`,
       `Motor: flujo de potencia Z-bus sobre el modelo exacto de pandapower para 8 760 horas × ${years} años; capacidad estática, por series de tiempo y envolventes verificadas.`,
       "API y web: FastAPI sirve resultados precalculados y evalúa solicitudes en vivo; esta interfaz los muestra."]
    : ["Ingest: SMHI, eSett, SE3 prices, SCB and OpenStreetMap, with a provenance record and hash for every file.",
       `Grid: ${houses} real houses connected to the mapped substation; radial feeders along the streets, cable cabinets and cables sized with Velander's formula.`,
       `Profiles: ${pool} simulated households (CREST) with SCB's household-size mix, PV with pvlib and real irradiance, heat pumps with real temperature, and EV charging.`,
       `Engine: Z-bus power flow on pandapower's exact model for 8 760 hours × ${years} years; static, time-series and verified envelope hosting capacity.`,
       "API and web: FastAPI serves precomputed results and evaluates requests live; this interface shows them."];
  return (
    <ol className="list">
      {steps.map((s) => <li key={s}>{s}</li>)}
    </ol>
  );
}

function strangOption(tk: Tokens, shifts: any[]) {
  return {
    grid: { left: 44, right: 12, top: 12, bottom: 34 },
    xAxis: { type: "value", ...axisStyle(tk), name: "min", nameLocation: "middle", nameGap: 24 },
    yAxis: { type: "value", ...axisStyle(tk), scale: true },
    tooltip: { trigger: "axis" },
    series: [
      { type: "line", data: shifts.map((s) => [s.shift_min, Number(s.morning_k.toFixed(4))]), lineStyle: { width: 2, color: tk.main }, itemStyle: { color: tk.main, borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
      { type: "line", data: shifts.map((s) => [s.shift_min, Number(s.afternoon_k.toFixed(4))]), lineStyle: { width: 2, color: tk.cat[0] }, itemStyle: { color: tk.cat[0], borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
    ],
  };
}

function shapeOption(tk: Tokens, sh: any) {
  const hours = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}`);
  return {
    grid: { left: 40, right: 12, top: 12, bottom: 28 },
    xAxis: { type: "category", data: hours, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 3 } },
    yAxis: { type: "value", ...axisStyle(tk), scale: true, name: "× mean" },
    tooltip: { trigger: "axis" },
    series: [
      { type: "line", data: sh.hour_of_day_sim, lineStyle: { width: 2, color: tk.main }, symbol: "none" },
      { type: "line", data: sh.hour_of_day_real, lineStyle: { width: 2, color: tk.cat[0] }, symbol: "none" },
    ],
  };
}

function weatherOption(tk: Tokens, w: any[]) {
  return {
    grid: { left: 48, right: 12, top: 12, bottom: 26 },
    xAxis: { type: "category", data: w.map((x) => String(x.year)), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), name: "kWh/m²", min: 0 },
    tooltip: { trigger: "axis" },
    series: [{ type: "bar", data: w.map((x) => Math.round(x.ghi_kwh_m2)), itemStyle: { color: tk.main, borderRadius: [tk.size.radius, tk.size.radius, 0, 0] }, barWidth: 18 }],
  };
}
