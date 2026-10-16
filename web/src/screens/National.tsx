import { useCallback, useMemo } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap } from "maplibre-gl";
import { MapView, fitTo } from "../components/MapView";
import { Chart, axisStyle } from "../components/Chart";
import { ChartOrTable, Chip, Legend, Loading, Missing } from "../components/bits";
import { useApi } from "../lib/api";
import { seqColor, tokens, type Tokens } from "../lib/colors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

const ZONES = ["SE1", "SE2", "SE3", "SE4"];
const SWEDEN_BBOX: [number, number, number, number] = [10.9, 55.2, 24.2, 69.1];
// production types in fixed categorical order (offshore wind and storage fold into "other")
const TYPES = ["hydro", "nuclear", "wind", "solar", "thermal", "other"] as const;

function slotColors(tk: Tokens): string[] {
  const dark = document.documentElement.getAttribute("data-theme") === "dark" ||
    (!document.documentElement.getAttribute("data-theme") && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  // slots 4–6 of the validated palette (light / dark steps)
  const extra = dark ? ["#c98500", "#d55181", "#008300"] : ["#eda100", "#e87ba4", "#008300"];
  return [tk.accent, tk.cat[0], tk.cat[2], ...extra];
}

export default function National() {
  const t = useT();
  const f = useFmt();
  const theme = useStore((s) => s.theme);
  const nat = useApi<any>("/national");
  const zones = useApi<any>("/zones");
  const N = nat.data?.data;

  const priceRange = useMemo(() => {
    if (!N) return [0, 1];
    const ps = ZONES.map((z) => N.zones[z].price_mean_sek_kwh);
    return [Math.min(...ps), Math.max(...ps)];
  }, [N]);

  const setup = useCallback((map: MLMap) => {
    if (!zones.data || !N) return;
    const tk = tokens();
    const gj = JSON.parse(JSON.stringify(zones.data));
    for (const ft of gj.features) {
      const z = ft.properties.zone;
      const p = N.zones[z]?.price_mean_sek_kwh ?? 0;
      ft.properties.color = seqColor(tk, (p - priceRange[0]) / Math.max(1e-6, priceRange[1] - priceRange[0]) * 0.85 + 0.1);
    }
    if (!map.getSource("zones")) map.addSource("zones", { type: "geojson", data: gj } as any);
    if (!map.getLayer("zones-fill")) {
      map.addLayer({ id: "zones-fill", type: "fill", source: "zones", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.78 } });
      map.addLayer({ id: "zones-line", type: "line", source: "zones", paint: { "line-color": tk.surface, "line-width": 2 } });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zones.data, N, theme]);

  const onReady = useCallback((map: MLMap) => {
    fitTo(map, SWEDEN_BBOX, 28);
    if (!zones.data || !N) return;
    // zone labels as HTML markers (no font server needed)
    for (const ft of zones.data.features) {
      const z = ft.properties.zone;
      const [lon, lat] = centroid(ft.geometry);
      const el = document.createElement("div");
      el.style.cssText = "background:var(--bg-surface);border:1px solid var(--border);border-radius:6px;padding:3px 8px;font:600 13px var(--font-sans);color:var(--text-primary);text-align:center;box-shadow:0 1px 0 rgba(0,0,0,.05)";
      const b = document.createElement("div");
      b.textContent = z;
      const s = document.createElement("div");
      s.style.cssText = "font-weight:500;font-size:12px;color:var(--text-secondary)";
      s.textContent = `${N.zones[z].price_mean_sek_kwh.toFixed(2)} kr/kWh`;
      el.append(b, s);
      new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    }
  }, [zones.data, N]);

  if (nat.isLoading || zones.isLoading) return <Loading />;
  if (!N || !zones.data) return <Missing />;
  const tk = tokens();

  return (
    <div className="split wide-panel">
      <div className="map-col">
        <div className="map-wrap">
          <MapView center={[17.5, 63.0]} zoom={3.6} setup={setup} onReady={onReady} ariaLabel={t("nat.title")}>
            <div className="map-legend">
              <div className="scale">
                <span>{t("nat.mapLegend", { year: N.year })}</span>
                <div className="scale-bar" style={{ background: `linear-gradient(90deg, ${tk.load.slice(1).join(",")})` }} />
                <div className="scale-ticks"><span>{f(priceRange[0], 2)}</span><span>{f(priceRange[1], 2)}</span></div>
              </div>
            </div>
          </MapView>
        </div>
      </div>
      <aside className="panel">
        <div className="stack">
          <h2 style={{ fontSize: "var(--text-h2)" }}>{t("nat.title")}</h2>
          <p className="caption">{t("nat.sub", { year: N.year })}</p>
          <div className="row"><Chip kind="real" /><span className="caption-muted">{t("nat.sources")}</span></div>
        </div>

        <section className="stack">
          <h3>{t("nat.prodcons")}</h3>
          <Legend items={[{ color: "var(--accent)", label: t("nat.production") }, { color: "var(--cat-1)", label: t("nat.consumption") }]} />
          <ChartOrTable
            chart={<Chart ariaLabel={t("nat.prodcons")} height={220} deps={[N]} build={(tk) => prodConsOption(tk, N, t)} />}
            table={
              <table className="data">
                <thead><tr><th>{t("nat.zone")}</th><th className="r">{t("nat.production")}</th><th className="r">{t("nat.consumption")}</th><th className="r">{t("nat.balance")}</th><th className="r">{t("nat.price")}</th></tr></thead>
                <tbody>{ZONES.map((z) => (
                  <tr key={z}><td>{z}</td><td className="r">{f(N.zones[z].production_twh, 1)}</td><td className="r">{f(N.zones[z].consumption_twh, 1)}</td><td className="r">{f(N.zones[z].balance_twh, 1)}</td><td className="r">{f(N.zones[z].price_mean_sek_kwh, 3)}</td></tr>
                ))}</tbody>
              </table>
            }
          />
        </section>

        <section className="stack">
          <h3>{t("nat.mix")}</h3>
          <Legend items={TYPES.map((ty, i) => ({ color: slotColors(tk)[i], label: t(`type.${ty}`) }))} />
          <Chart ariaLabel={t("nat.mix")} height={200} deps={[N]} build={(tk) => mixOption(tk, N, t)} />
        </section>

        <section className="stack">
          <h3>{t("nat.neg")}</h3>
          <Chart ariaLabel={t("nat.neg")} height={150} deps={[N]} build={(tk) => negOption(tk, N)} />
        </section>

        <section className="stack">
          <h3>{t("nat.capTitle")}</h3>
          <p className="caption">{t("nat.capText")}</p>
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
            <li><a href="https://www.iea.org/reports/electricity-2026/grids" target="_blank" rel="noreferrer">IEA, Electricity 2026: Grids</a></li>
            {N.capacity_links.map((l: any) => (
              <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer">{l.name}</a> <Chip kind="reference" /></li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}

function centroid(geom: any): [number, number] {
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.coordinates;
  let best: number[][] = polys[0][0];
  let area = 0;
  for (const p of polys) {
    const ring = p[0];
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    if (Math.abs(a) > area) {
      area = Math.abs(a);
      best = ring;
    }
  }
  const xs = best.map((c) => c[0]);
  const ys = best.map((c) => c[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

function prodConsOption(tk: Tokens, N: any, t: (k: string) => string) {
  return {
    grid: { left: 40, right: 12, top: 12, bottom: 26 },
    xAxis: { type: "category", data: ZONES, ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), name: "TWh" },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    series: [
      { type: "bar", name: t("nat.production"), data: ZONES.map((z) => Number(N.zones[z].production_twh.toFixed(1))), itemStyle: { color: tk.accent, borderRadius: [4, 4, 0, 0] }, barWidth: 20, barGap: "10%" },
      { type: "bar", name: t("nat.consumption"), data: ZONES.map((z) => Number(N.zones[z].consumption_twh.toFixed(1))), itemStyle: { color: tk.cat[0], borderRadius: [4, 4, 0, 0] }, barWidth: 20 },
    ],
  };
}

function mixOption(tk: Tokens, N: any, t: (k: string) => string) {
  const colors = slotColors(tk);
  const val = (z: string, ty: string) => {
    const m = N.zones[z].mix_twh;
    if (ty === "wind") return m.wind + (m.windOffshore ?? 0);
    if (ty === "other") return (m.other ?? 0) + (m.energyStorage ?? 0);
    return m[ty] ?? 0;
  };
  return {
    grid: { left: 40, right: 16, top: 6, bottom: 24 },
    xAxis: { type: "value", ...axisStyle(tk), name: "TWh" },
    yAxis: { type: "category", data: ZONES, inverse: true, ...axisStyle(tk), splitLine: { show: false } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => `${v.toFixed(1)} TWh` },
    series: TYPES.map((ty, i) => ({
      type: "bar", stack: "mix", name: t(`type.${ty}`), barWidth: 18,
      data: ZONES.map((z) => Number(val(z, ty).toFixed(2))),
      itemStyle: { color: colors[i], borderColor: tk.surface, borderWidth: 1 },
    })),
  };
}

function negOption(tk: Tokens, N: any) {
  return {
    grid: { left: 40, right: 40, top: 6, bottom: 22 },
    xAxis: { type: "value", ...axisStyle(tk) },
    yAxis: { type: "category", data: ZONES, inverse: true, ...axisStyle(tk), splitLine: { show: false } },
    tooltip: { trigger: "item", formatter: (p: any) => `${p.name}: <b>${p.value}</b> h` },
    series: [{
      type: "bar", barWidth: 14, data: ZONES.map((z) => N.zones[z].negative_price_hours),
      itemStyle: { color: tk.accent, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: "right", color: tk.text, formatter: "{c} h" },
    }],
  };
}

