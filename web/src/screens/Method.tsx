import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { ChartOrTable, Chip, Legend, Loading, Status, Tile } from "../components/bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

const TAGS = ["sourced", "regulation", "library", "derived", "team-assumption"];

const LIMITS: Record<"en" | "es", string[]> = {
  en: [
    "Envelopes use perfect foresight of each household's load and solar production; real envelopes are issued from forecasts, so curtailment would be somewhat higher.",
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
    "Las envolventes usan previsión perfecta del consumo y la producción solar de cada vivienda; las reales se calculan con pronósticos, así que el recorte sería algo mayor.",
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
  const [tag, setTag] = useState<string>("all");
  const V = val.data?.data;

  const statusByKey = useMemo(() => {
    const m = new Map<string, any>();
    (status.data?.results ?? []).forEach((r: any) => m.set(r.key, r));
    return m;
  }, [status.data]);

  if (src.isLoading || ass.isLoading) return <Loading />;
  const assumptions = (ass.data?.assumptions ?? []).filter((a: any) => tag === "all" || a.tag === tag);
  const counts = TAGS.map((tg) => (ass.data?.assumptions ?? []).filter((a: any) => a.tag === tg).length);

  return (
    <div className="page">
      <div className="page-inner">
        <section className="stack">
          <h1 className="headline" style={{ fontSize: "var(--t-2xl)" }}>{t("met.title")}</h1>
          <p className="headline-sub">{t("met.sub")}</p>
        </section>

        <section className="block">
          <div className="block-head"><h2>{t("met.how")}</h2></div>
          <HowItWorks
            lang={lang}
            houses={meta.data?.grid?.customers ?? "–"}
            pool={V?.households?.n_pool ?? "–"}
            years={meta.data?.weather_years?.length ?? "–"}
          />
        </section>

        {/* ---------------------------------------------------------------- sources */}
        <section className="block">
          <div className="block-head">
            <h2>{t("met.sources")}</h2>
            <span className="caption-muted">{status.data?.checked_at ? `${t("met.status")}: ${new Date(status.data.checked_at).toLocaleTimeString(lang === "es" ? "es-ES" : "en-GB")}` : ""}</span>
          </div>
          <div className="src-list" style={{ marginTop: 10 }}>
            {src.data?.sources.map((s: any) => {
              const st = statusByKey.get(s.key);
              return (
                <div key={s.key} className="src">
                  <div>
                    <span className="src-name">{s.name}</span> <Chip kind={s.tag} />
                  </div>
                  <div>
                    {st ? (st.ok ? <Status kind="good">{t("met.online")} · {st.ms} ms</Status> : <Status kind="serious">{t("met.offline")}</Status>) : null}
                  </div>
                  <div className="src-meta">
                    {s.provider}. {s.used_for}. <em>{s.license}</em>.{" "}
                    {s.files.length > 0 && (s.files.length === 1 ? t("met.file") : t("met.files", { n: s.files.length }))}
                    {s.files.length > 0 && `, ${s.files[0].fetched_at.slice(0, 10)}`}
                    {s.homepage && <> · <a href={s.homepage} target="_blank" rel="noreferrer">{s.homepage.replace(/^https?:\/\//, "").split("/")[0]}</a></>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ---------------------------------------------------------------- assumptions */}
        <section className="block">
          <div className="block-head">
            <h2>{t("met.assumptions")}</h2>
            <div className="seg">
              <button aria-pressed={tag === "all"} onClick={() => setTag("all")}>{t("met.all")}</button>
              {TAGS.map((tg, i) => counts[i] > 0 && (
                <button key={tg} aria-pressed={tag === tg} onClick={() => setTag(tg)}>{t(`met.tag.${tg}`)} ({counts[i]})</button>
              ))}
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="data">
              <thead><tr><th>{t("tbl.input")}</th><th className="r">{t("tbl.value")}</th><th>{t("tbl.tag")}</th><th>{t("tbl.source")}</th></tr></thead>
              <tbody>
                {assumptions.map((a: any) => (
                  <tr key={a.key}>
                    <td style={{ minWidth: 220 }}>{a.label}</td>
                    <td className="r" style={{ whiteSpace: "nowrap" }}>{Array.isArray(a.value) ? a.value.join(", ") : String(a.value)} {a.unit}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{t(`met.tag.${a.tag}`)}</td>
                    <td>{a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.source}</a> : a.source}{a.note ? <div className="caption-muted">{a.note}</div> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* ---------------------------------------------------------------- validation */}
        {V && (
          <>
            <section className="block stack">
              <div className="block-head"><h2>{t("met.pf")}</h2></div>
              <div className="tiles" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
                <Tile label={t("met.pf.dv")} value={V.powerflow.max_abs_dv_pu.toExponential(1)} unit="pu" note={`${V.powerflow.cases} random operating points`} />
                <Tile label={t("met.pf.dl")} value={V.powerflow.max_abs_dloading_pct.toExponential(1)} unit="%" />
                <Tile label={t("met.pf.speed")} value={f(V.powerflow.zbus_seconds_per_year, 1)} unit="s" note={t("met.pf.size", { b: V.powerflow.buses, l: V.powerflow.lines })} />
                <Tile label={t("met.pf.nr")} value={f(V.powerflow.pandapower_ms_per_case, 0)} unit="ms" />
              </div>
            </section>

            <div className="grid-2">
              <section className="block">
                <div className="block-head"><h2>{t("met.strang")}</h2></div>
                <p className="caption">{t("met.strang.caption")}</p>
                <Legend items={[{ color: "var(--series-1)", label: t("met.strang.morning"), line: true }, { color: "var(--series-2)", label: t("met.strang.afternoon"), line: true }]} />
                <Chart ariaLabel={t("met.strang")} height={220} deps={[V]} build={(tk) => strangOption(tk, V.strang_timestamp.shifts)} />
              </section>
              {V.household_shape && (
                <section className="block">
                  <div className="block-head"><h2>{t("met.shape")}</h2></div>
                  <p className="caption">{t("met.shape.caption", { year: V.household_shape.year, r: f(V.household_shape.corr_hour_of_day, 2) })}</p>
                  <Legend items={[{ color: "var(--series-1)", label: t("met.shape.sim"), line: true }, { color: "var(--series-2)", label: t("met.shape.real"), line: true }]} />
                  <Chart ariaLabel={t("met.shape")} height={220} deps={[V]} build={(tk) => shapeOption(tk, V.household_shape)} />
                </section>
              )}
            </div>

            <section className="block">
              <div className="block-head"><h2>{t("met.weather")}</h2></div>
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
              <div className="tiles" style={{ marginTop: 12, gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
                <Tile label="Diffuse closure vs SMHI (param 122)" value={f(V.diffuse_closure?.mean_abs_w_m2, 2)} unit="W/m²" note="mean absolute difference, 2025" />
                <Tile label="PV yield, median roof" value={f(V.pv_yield_kwh_per_kwp.median)} unit="kWh/kWp" note={`${V.pv_yield_kwh_per_kwp.year}`} />
                <Tile label="Household energy, P10–P90" value={`${f(V.households.p10_kwh)}–${f(V.households.p90_kwh)}`} unit="kWh" note={`CREST raw mean ${f(V.households.crest_raw_mean_kwh)} kWh, scaled ×${f(V.households.scale_factor, 2)}`} />
                <Tile label="Household peak, 10-min vs hourly" value={`${f(V.households.peak_10min_kw_median, 1)} / ${f(V.households.peak_hourly_kw_median, 1)}`} unit="kW" note="median household" />
              </div>
            </section>
          </>
        )}

        {/* ---------------------------------------------------------------- limitations & refs */}
        <div className="grid-2">
          <section className="block">
            <div className="block-head"><h2>{t("met.limits")}</h2></div>
            <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 8 }} className="caption">
              {LIMITS[lang].map((l) => <li key={l}>{l}</li>)}
            </ul>
          </section>
          <section className="block">
            <div className="block-head"><h2>{t("met.refs")}</h2></div>
            <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 8 }} className="caption">
              {REFS.map(([r, u]) => <li key={r}>{u ? <a href={u} target="_blank" rel="noreferrer">{r}</a> : r}</li>)}
            </ol>
          </section>
        </div>
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
    <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 8 }}>
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
      { type: "line", data: shifts.map((s) => [s.shift_min, Number(s.morning_k.toFixed(4))]), lineStyle: { width: 2, color: tk.s1 }, itemStyle: { color: tk.s1, borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
      { type: "line", data: shifts.map((s) => [s.shift_min, Number(s.afternoon_k.toFixed(4))]), lineStyle: { width: 2, color: tk.s2 }, itemStyle: { color: tk.s2, borderColor: tk.surface, borderWidth: 2 }, symbolSize: 8 },
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
      { type: "line", data: sh.hour_of_day_sim, lineStyle: { width: 2, color: tk.s1 }, symbol: "none" },
      { type: "line", data: sh.hour_of_day_real, lineStyle: { width: 2, color: tk.s2 }, symbol: "none" },
    ],
  };
}

function weatherOption(tk: Tokens, w: any[]) {
  return {
    grid: { left: 48, right: 12, top: 12, bottom: 26 },
    xAxis: { type: "category", data: w.map((x) => String(x.year)), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), name: "kWh/m²", min: 0 },
    tooltip: { trigger: "axis" },
    series: [{ type: "bar", data: w.map((x) => Math.round(x.ghi_kwh_m2)), itemStyle: { color: tk.s1, borderRadius: [4, 4, 0, 0] }, barWidth: 18 }],
  };
}
