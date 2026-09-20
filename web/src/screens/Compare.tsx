import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { ChartOrTable, Chip, Legend, Loading, Missing, Status, Tile } from "../components/bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { monthNames, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";
import { GridPreview } from "../components/GridPreview";
import { Icon } from "../components/Icon";

const MEASURES = ["static", "fixed_cap", "timeseries", "doe"] as const;

export default function Compare() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const hosting = useApi<any>("/hosting");
  const show = useApi<any>("/showcase");
  const risk = useApi<any>("/risk");
  const val = useApi<any>("/validation");
  const [heat, setHeat] = useState<"limited" | "unc">("limited");

  const H = hosting.data?.data;
  const S = show.data?.data?.showcase;
  const HM = show.data?.data?.heatmaps;
  const R = risk.data?.data;
  const PF = val.data?.data?.powerflow;

  const zDoeMedian = useMemo(() => {
    if (!H) return null;
    const zs = H.summary.per_order.map((o: any) => o.z_doe_pct).sort((a: number, b: number) => a - b);
    return zs[Math.floor(zs.length / 2)];
  }, [H]);

  if (hosting.isLoading || show.isLoading) return <Loading />;
  if (!H || !S) return <Missing />;

  const m = H.summary.measures;
  const zStar = H.summary.z_star_pct;
  const staticKw = m.static.kw_ac.median;
  const doeKw = m.doe.kw_ac.median;
  const years = H.summary.years;
  const ratio = doeKw / staticKw;
  const pol = S.policies;
  const ver = S.verification;
  const fixedZ = (H.curve.points as any[]).map((p) => p.z_fixed_pct).filter((v: number) => v > 0);

  return (
    <div className="page">
      <div className="page-inner">
        {/* ---------------------------------------------------------------- headline */}
        <section className="overview-hero">
          <div className="overview-copy">
            <span className="eyebrow"><span className="signal-line" />{t("overview.eyebrow")}</span>
            <h1 className="headline">{t("overview.title")}<br /><span>{t("overview.titleAccent")}</span></h1>
            <p className="headline-sub">{t("cmp.headline", { static: f(staticKw), doe: f(doeKw) })}</p>
            <div className="hero-actions"><a className="btn" href="#/twin">{t("overview.explore")}<Icon name="arrow" size={18} /></a><a className="btn ghost" href="#/request"><Icon name="request" size={17} />{t("overview.request")}</a></div>
            <span className="hero-context">{t("overview.sample", { n: H.summary.n_orders, years: years.length })}</span>
          </div>
          <GridPreview />
        </section>
          <div className="hero-figures" aria-label={t("overview.metrics")}>
            <div className="figure">
              <div className="metric-top"><span>{t("overview.baseline")}</span><Icon name="compare" /></div>
              <div className="value">{f(staticKw)}<small>kW</small></div>
              <div className="label"><span className="mark" style={{ background: "var(--deemph)" }} />{t("cmp.fig.static")}</div>
            </div>
            <div className="figure featured">
              <div className="metric-top"><span>{t("overview.dynamic")}</span><Icon name="bolt" /></div>
              <div className="value">{f(doeKw)}<small>kW</small></div>
              <div className="label"><span className="mark" style={{ background: "var(--series-1)" }} />{t("cmp.fig.doe")}</div>
            </div>
            <div className="figure">
              <div className="metric-top"><span>{t("overview.uplift")}</span><Icon name="scenarios" /></div>
              <div className="value">×{f(ratio, 1)}</div>
              <div className="label">{t("cmp.fig.gain")}</div>
            </div>
            <div className="figure">
              <div className="metric-top"><span>{t("overview.curtailment")}</span><Icon name="sun" /></div>
              <div className="value">{f(zDoeMedian, 1)}<small>%</small></div>
              <div className="label">{t("overview.curtailmentNote")}</div>
            </div>
          </div>
          <div className="data-provenance">
            <Chip kind="real" /><span className="caption-muted">{t("cmp.chips.real")}</span>
            <Chip kind="synthetic" /><span className="caption-muted">{t("cmp.chips.synthetic")}</span>
          </div>

        {/* ---------------------------------------------------------------- measures */}
        <section className="block">
          <div className="block-head"><h2>{t("cmp.bars")}</h2></div>
          <p className="caption">{t("cmp.bars.caption")}</p>
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
        </section>

        <div className="grid-2">
          {/* -------------------------------------------------------------- curve */}
          <section className="block">
            <div className="block-head"><h2>{t("cmp.curve")}</h2></div>
            <p className="caption">{t("cmp.curve.caption")}</p>
            <Chart ariaLabel={t("cmp.curve")} height={300} deps={[H]} build={(tk) => curveOption(tk, H, t, zStar)} />
            {fixedZ.length > 0 && (
              <p className="caption">
                {t("cmp.curve.fixedNote", { a: f(Math.min(...fixedZ)), b: f(Math.max(...fixedZ)) })}
              </p>
            )}
          </section>

          {/* -------------------------------------------------------------- verification */}
          <section className="block stack">
            <div className="block-head"><h2>{t("cmp.verify")}</h2></div>
            <div className="tiles">
              <Tile label={t("cmp.verify.doe")} value={<Status kind={ver.doe_full_year_violations === 0 ? "good" : "critical"}>{f(ver.doe_full_year_violations)}</Status>} note={`${S.year}, 8 760 h`} />
              <Tile label={t("cmp.verify.unc")} value={<Status kind="critical">{f(ver.uncontrolled_violation_hours)}</Status>} note={`${S.year}`} />
              <Tile label={t("cmp.verify.vmax")} value={f(ver.doe_max_voltage_pu, 3)} unit="pu" />
              <Tile label={t("cmp.verify.trafo")} value={f(ver.doe_max_trafo_loading_pct, 1)} unit="%" />
              <Tile label={t("cmp.verify.screen")} value={f(ver.screen_missed_hours)} note={t("cmp.verify.candidates", { n: f(ver.screen_candidate_hours) })} />
              <Tile label={t("cmp.verify.zbus")} value={PF ? PF.max_abs_dv_pu.toExponential(0) : "–"} unit="pu" note={PF ? t("cmp.verify.zbusNote", { n: PF.cases }) : undefined} />
            </div>
            <p className="caption">{t("cmp.binding", { v: f(ver.uncontrolled_max_voltage_pu, 3) })}</p>
          </section>
        </div>

        {/* ---------------------------------------------------------------- heatmap */}
        {HM && (
          <section className="block">
            <div className="block-head">
              <h2>{t("cmp.heat", { year: HM.year })}</h2>
              <div className="seg">
                <button aria-pressed={heat === "limited"} onClick={() => setHeat("limited")}>{t("cmp.heat.limited")}</button>
                <button aria-pressed={heat === "unc"} onClick={() => setHeat("unc")}>{t("scn.uncontrolled")}</button>
              </div>
            </div>
            <p className="caption">{t("cmp.heat.caption")}</p>
            <Chart ariaLabel={t("cmp.heat", { year: HM.year })} height={260} deps={[HM, heat, lang]} build={(tk) => heatOption(tk, HM, heat, lang, t)} />
          </section>
        )}

        <div className="grid-2">
          {/* -------------------------------------------------------------- fairness */}
          <section className="block">
            <div className="block-head"><h2>{t("cmp.fair")}</h2></div>
            <p className="caption">{t("cmp.fair.caption")}</p>
            <Legend items={[
              { color: "var(--series-1)", label: t("pol.maxmin") },
              { color: "var(--series-2)", label: t("pol.prorata") },
            ]} />
            <ChartOrTable
              chart={<Chart ariaLabel={t("cmp.fair")} height={270} deps={[S]} build={(tk) => fairOption(tk, pol, t)} />}
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
          </section>

          {/* -------------------------------------------------------------- risk */}
          {R && (
            <section className="block stack">
              <div className="block-head"><h2>{t("cmp.risk")}</h2></div>
              <p className="caption">{t("cmp.risk.caption")}</p>
              <div className="tiles" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
                <Tile label={t("cmp.risk.mean")} value={f(R.stats.mean_z_pct, 1)} unit="%" />
                <Tile label={t("cmp.risk.p90")} value={f(R.stats.p90_z_pct, 1)} unit="%" />
                <Tile label={t("cmp.risk.worst")} value={f(R.stats.worst_z_pct, 1)} unit="%" />
                <Tile label={t("cmp.risk.cvar")} value={f(R.stats.cvar95_z_pct, 1)} unit="%" />
              </div>
              <Chart ariaLabel={t("cmp.risk")} height={200} deps={[R]} build={(tk) => riskOption(tk, R, zStar)} />
            </section>
          )}
        </div>

        {/* ---------------------------------------------------------------- tornado */}
        {H.tornado && (
          <section className="block">
            <div className="block-head"><h2>{t("cmp.tornado")}</h2></div>
            <p className="caption">{t("cmp.tornado.caption")}</p>
            <Legend items={[
              { color: "var(--series-1)", label: t("cmp.fig.doe") },
              { color: "var(--deemph)", label: t("cmp.fig.static") },
            ]} />
            <ChartOrTable
              chart={<Chart ariaLabel={t("cmp.tornado")} height={330} deps={[H.tornado]} build={(tk, width) => tornadoOption(tk, H.tornado, t, width)} />}
              table={
                <table className="data">
                  <thead><tr><th>{t("tbl.input")}</th><th>{t("tbl.value")}</th><th className="r">{t("tbl.staticKw")}</th><th className="r">{t("tbl.tsKw")}</th><th className="r">{t("tbl.doeKw")}</th></tr></thead>
                  <tbody>
                    <tr><td>{t("tbl.base")}</td><td>–</td><td className="r">{f(H.tornado.base.static_kw)}</td><td className="r">{f(H.tornado.base.timeseries_kw)}</td><td className="r">{f(H.tornado.base.doe_kw)}</td></tr>
                    {H.tornado.cases.map((c: any) => (
                      <tr key={c.label + c.level}><td>{c.label}</td><td>{c.level}</td><td className="r">{f(c.static_kw)}</td><td className="r">{f(c.timeseries_kw)}</td><td className="r">{f(c.doe_kw)}</td></tr>
                    ))}
                  </tbody>
                </table>
              }
            />
          </section>
        )}
      </div>
    </div>
  );
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
    yAxis: { type: "category", data: cats, inverse: true, ...axisStyle(tk), axisLabel: { color: tk.ink, fontFamily: tk.font, fontSize: compact ? 10 : 13, width: compact ? 106 : 240, overflow: "break" } },
    tooltip: { trigger: "item", formatter: (p: any) => `${cats[p.dataIndex]}<br/><b>${Math.round(vals[p.dataIndex])} kW</b> (P10 ${Math.round(lo[p.dataIndex])}–P90 ${Math.round(hi[p.dataIndex])})` },
    series: [
      {
        type: "bar",
        barWidth: 22,
        data: vals.map((v, i) => ({ value: v, itemStyle: { color: MEASURES[i] === "doe" ? tk.s1 : tk.deemph, borderRadius: [0, 4, 4, 0] } })),
        label: { show: true, position: "right", distance: compact ? 8 : 26, fontSize: compact ? 10 : 12, color: tk.ink, fontWeight: 600, formatter: (p: any) => `${Math.round(p.value)} kW` },
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
              { type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: tk.ink, lineWidth: 1.5 } },
              { type: "line", shape: { x1: a[0], y1: a[1] - 5, x2: a[0], y2: a[1] + 5 }, style: { stroke: tk.ink, lineWidth: 1.5 } },
              { type: "line", shape: { x1: b[0], y1: b[1] - 5, x2: b[0], y2: b[1] + 5 }, style: { stroke: tk.ink, lineWidth: 1.5 } },
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

function curveOption(tk: Tokens, H: any, t: (k: string, v?: any) => string, zStar: number) {
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
        return `<b>${Math.round(p.kw_ac)} kW</b> · ${p.adopters} houses<br/>${t("cmp.curve.doe")}: <b>${p.z_doe_pct.toFixed(2)} %</b> (${minY[i].toFixed(1)}–${maxY[i].toFixed(1)} %)<br/>${t("cmp.curve.fixed")}: <b>${p.z_fixed_pct.toFixed(1)} %</b>`;
      },
    },
    series: [
      { type: "line", data: xs.map((x, i) => [x, minY[i]]), stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } },
      { type: "line", data: xs.map((x, i) => [x, maxY[i] - minY[i]]), stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: tk.s1, opacity: 0.12 }, silent: true, tooltip: { show: false } },
      {
        type: "line", name: t("cmp.curve.doe"), data: xs.map((x, i) => [x, pts[i].z_doe_pct]), symbol: "circle", symbolSize: 7,
        lineStyle: { width: 2, color: tk.s1 }, itemStyle: { color: tk.s1, borderColor: tk.surface, borderWidth: 2 },
        markLine: {
          symbol: "none", silent: true,
          lineStyle: { color: tk.baseline, type: "solid", width: 1 },
          label: { color: tk.ink2, fontFamily: tk.font, fontSize: 11 },
          data: [
            { yAxis: zStar, label: { formatter: t("cmp.curve.limit", { z: zStar }), position: "insideStartTop" } },
            ...marks.map((mk) => ({ xAxis: mk.x, label: { formatter: mk.name, position: "insideEndTop", rotate: 90 } })),
          ],
        },
      },
    ],
  };
}

function heatOption(tk: Tokens, HM: any, which: "limited" | "unc", lang: string, t: (k: string) => string) {
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
  const monthStarts = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  return {
    grid: { left: 44, right: 80, top: 8, bottom: 30 },
    xAxis: {
      type: "category", data: Array.from({ length: HM.days }, (_, i) => i), ...axisStyle(tk), splitLine: { show: false },
      axisLabel: { color: tk.muted, interval: (i: number) => monthStarts.includes(i), formatter: (v: string) => months[monthStarts.indexOf(Number(v))] ?? "" },
    },
    yAxis: { type: "category", data: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}:00`), inverse: true, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 5 } },
    visualMap: {
      min: which === "limited" ? 1 : 0, max: which === "limited" ? Math.max(1, vmax) : 130, calculable: false, orient: "vertical", right: 0, top: "middle",
      itemHeight: 140, itemWidth: 10,
      textStyle: { color: tk.muted, fontFamily: tk.font },
      inRange: { color: which === "limited" ? [tk.seq[1], tk.seq[3], tk.seq[5], tk.seq[6]] : [tk.seq[0], tk.seq[2], tk.seq[4], tk.seq[6], tk.critical] },
      text: which === "limited" ? [String(vmax), "1"] : ["130 %", "0 %"],
    },
    tooltip: {
      formatter: (p: any) => {
        const [d, h, v] = p.value;
        const date = new Date(Date.UTC(HM.year, 0, 1) + d * 86400000);
        const label = `${date.getUTCDate()} ${months[date.getUTCMonth()]} ${String(h).padStart(2, "0")}:00`;
        return which === "limited" ? `${label}<br/>${t("cmp.heat.limited")}: <b>${v}</b>` : `${label}<br/>${t("twin.trafo")}: <b>${v.toFixed(0)} %</b>`;
      },
    },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: tk.surface, borderWidth: 0 }, progressive: 0 }],
  };
}

function fairOption(tk: Tokens, pol: any, t: (k: string, v?: any) => string) {
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
    tooltip: { trigger: "item", formatter: (p: any) => `${p.seriesName}<br/>${t("cmp.tt.house", { id: p.value[2], m: Math.round(p.value[0]) })}<br/>${t("cmp.tt.curtailed")}: <b>${p.value[1].toFixed(2)} %</b>` },
    series: [mk("maxmin", tk.s1), mk("prorata", tk.s2)],
  };
}

function riskOption(tk: Tokens, R: any, zStar: number) {
  const cases = R.cases as any[];
  const years = Array.from(new Set(cases.map((c) => c.year))).sort();
  return {
    grid: { left: 44, right: 16, top: 12, bottom: 28 },
    xAxis: { type: "category", data: years.map(String), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), axisLabel: { color: tk.muted, formatter: "{value} %" } },
    tooltip: { trigger: "item", formatter: (p: any) => `${p.value[0]}<br/><b>${p.value[1].toFixed(2)} %</b>` },
    series: [{
      type: "scatter", symbolSize: 8, itemStyle: { color: tk.s1, borderColor: tk.surface, borderWidth: 2 },
      data: cases.map((c) => [String(c.year), c.z_pct]),
      markLine: { symbol: "none", silent: true, lineStyle: { color: tk.baseline, type: "solid" }, label: { color: tk.ink2, fontFamily: tk.font, formatter: `${zStar} %`, position: "insideEndTop" }, data: [{ yAxis: zStar }] },
    }],
  };
}

function tornadoOption(tk: Tokens, T: any, t: (k: string) => string, width: number) {
  const compact = width < 520;
  const groups: Record<string, any[]> = {};
  for (const c of T.cases) (groups[c.label] ||= []).push(c);
  const labels = Object.keys(groups);
  const rng = (key: "doe_kw" | "static_kw") => labels.map((l) => {
    const vs = groups[l].map((c) => c[key]);
    return [Math.min(...vs), Math.max(...vs)];
  });
  const d = rng("doe_kw");
  const s = rng("static_kw");
  const levels = (l: string) => groups[l].map((c) => `${c.level}: ${t("m.static")} ${Math.round(c.static_kw)} kW, ${t("cmp.fig.doe")} ${Math.round(c.doe_kw)} kW`).join("<br/>");
  return {
    grid: { left: compact ? 116 : 250, right: compact ? 20 : 40, top: 10, bottom: 34 },
    xAxis: { type: "value", ...axisStyle(tk), name: "kW AC", nameLocation: "middle", nameGap: 24, min: 0 },
    yAxis: { type: "category", data: labels, inverse: true, ...axisStyle(tk), axisLabel: { color: tk.ink, fontFamily: tk.font, fontSize: compact ? 9 : 12, width: compact ? 106 : 240, overflow: "truncate" } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (ps: any[]) => `<b>${ps[0].name}</b><br/>${levels(ps[0].name)}` },
    series: [
      { type: "bar", stack: "d", data: d.map((x) => x[0]), itemStyle: { color: "transparent" }, barWidth: 10, silent: true },
      { type: "bar", stack: "d", data: d.map((x) => Math.max(x[1] - x[0], 2)), itemStyle: { color: tk.s1, borderRadius: 4 }, barWidth: 10 },
      { type: "bar", stack: "s", data: s.map((x) => x[0]), itemStyle: { color: "transparent" }, barWidth: 10, silent: true },
      { type: "bar", stack: "s", data: s.map((x) => Math.max(x[1] - x[0], 2)), itemStyle: { color: tk.deemph, borderRadius: 4 }, barWidth: 10 },
      { type: "scatter", data: labels.map((l) => [T.base.doe_kw, l]), symbolSize: 9, itemStyle: { color: tk.ink, borderColor: tk.surface, borderWidth: 2 }, z: 5, silent: true },
      { type: "scatter", data: labels.map((l) => [T.base.static_kw, l]), symbolSize: 9, itemStyle: { color: tk.ink2, borderColor: tk.surface, borderWidth: 2 }, z: 5, silent: true },
    ],
  };
}

