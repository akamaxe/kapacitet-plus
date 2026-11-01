import { useCallback, useEffect, useMemo, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap } from "maplibre-gl";
import { MapView, fitTo } from "../components/MapView";
import { Chart, axisStyle } from "../components/Chart";
import { SplitSkeleton } from "../components/Skeleton";
import { ChartOrTable, Chip, Legend, Missing } from "../components/bits";
import { useApi } from "../lib/api";
import { balanceColor, rampCss, tokens, type Tokens } from "../lib/colors";
import { round, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

const ZONES = ["SE1", "SE2", "SE3", "SE4"];
const SWEDEN_BBOX: [number, number, number, number] = [10.9, 55.2, 24.2, 69.1];
// production types in fixed categorical order (offshore wind and storage fold into "other")
const TYPES = ["hydro", "nuclear", "wind", "solar", "thermal", "other"] as const;

/** 5.1: the basemap's own labels are off in the national view, so no foreign toponym can
    appear. Our zone labels are HTML markers and are unaffected. */
function hideBasemapLabels(map: MLMap) {
  for (const layer of map.getStyle()?.layers ?? []) {
    if (layer.type === "symbol") map.setLayoutProperty(layer.id, "visibility", "none");
  }
}

function slotColors(tk: Tokens): string[] {
  // The production mix of DESIGN.md: six categorical colours, in order, each
  // already given a light and a dark step by the theme.
  return tk.cat;
}

export default function National() {
  const t = useT();
  const f = useFmt();
  const theme = useStore((s) => s.theme);
  const nat = useApi<any>("/national");
  // today's price and temperature, live (the API caches them for ten minutes)
  const now = useApi<any>("/now", { refetchMs: 10 * 60 * 1000 });
  const lang = useStore((s) => s.lang);
  const zones = useApi<any>("/zones");
  const N = nat.data?.data;

  // the map shows what the title says: where a zone makes more than it uses, and where it
  // uses more than it makes. The largest balance either way sets the end of the scale.
  const span = useMemo(() => {
    if (!N) return 1;
    return Math.max(...ZONES.map((z) => Math.abs(N.zones[z].balance_twh)), 1e-6);
  }, [N]);

  const setup = useCallback((map: MLMap) => {
    if (!zones.data || !N) return;
    const tk = tokens();
    const gj = JSON.parse(JSON.stringify(zones.data));
    for (const ft of gj.features) {
      const z = ft.properties.zone;
      ft.properties.color = balanceColor(tk, (N.zones[z]?.balance_twh ?? 0) / span);
    }
    if (!map.getSource("zones")) map.addSource("zones", { type: "geojson", data: gj } as any);
    if (!map.getLayer("zones-fill")) {
      map.addLayer({ id: "zones-fill", type: "fill", source: "zones", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.78 } });
      map.addLayer({ id: "zones-line", type: "line", source: "zones", paint: { "line-color": tk.surface, "line-width": 2 } });
    }
    hideBasemapLabels(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zones.data, N, theme]);

  // the zone labels are HTML markers, so their text is set again whenever the language changes
  const labels = useRef<{ zone: string; b: HTMLDivElement; s: HTMLElement }[]>([]);
  const labelText = useCallback((z: string) => {
    const bal = N.zones[z].balance_twh;
    return [`${z}  ${bal > 0 ? "+" : bal < 0 ? "−" : ""}${f(Math.abs(bal), 1)} TWh`, `${f(N.zones[z].price_mean_sek_kwh, 2)} kr/kWh`];
  }, [N, f]);
  useEffect(() => {
    labels.current.forEach(({ zone, b, s }) => {
      const [top, bottom] = labelText(zone);
      b.textContent = top;
      s.textContent = bottom;
    });
  }, [labelText]);

  const onReady = useCallback((map: MLMap) => {
    fitTo(map, SWEDEN_BBOX, 28);
    if (!zones.data || !N) return;
    labels.current = [];
    // zone labels as HTML markers (no font server needed)
    for (const ft of zones.data.features) {
      const z = ft.properties.zone;
      const [lon, lat] = centroid(ft.geometry);
      // §5.3: flat text on the map, no box and no pin
      const el = document.createElement("div");
      el.className = "zone-label";
      const b = document.createElement("div");
      const s = document.createElement("small");
      [b.textContent, s.textContent] = labelText(z);
      labels.current.push({ zone: z, b, s });
      el.append(b, s);
      new maplibregl.Marker({ element: el }).setLngLat([lon, lat]).addTo(map);
    }
  }, [zones.data, N, labelText]);

  if (nat.isLoading || zones.isLoading) return <SplitSkeleton wide />;
  if (!N || !zones.data) return <Missing of={[nat, zones]} />;
  const tk = tokens();

  return (
    <div className="split wide-panel">
      <div className="map-col">
        <div className="map-wrap">
          <MapView center={[17.5, 63.0]} zoom={3.6} setup={setup} onReady={onReady} ariaLabel={t("nat.title")}>
            <div className="map-legend">
              <div className="scale">
                <span>{t("nat.mapLegendBalance", { year: N.year })}</span>
                <div className="scale-bar" style={{ background: rampCss((x) => balanceColor(tk, 2 * x - 1)) }} />
                <div className="scale-ticks">
                  <span>−{f(span, 0)}</span><span>0</span><span>+{f(span, 0)} TWh</span>
                </div>
                <div className="scale-ticks caption-muted"><span>{t("nat.usesMore")}</span><span>{t("nat.makesMore")}</span></div>
              </div>
            </div>
          </MapView>
        </div>
      </div>
      <aside className="panel">
        <div className="stack">
          <h1 className="panel-title">{t("nat.title")}</h1>
          <p className="caption">{t("nat.sub", { year: N.year })}</p>
          <div className="row"><Chip kind="real" /><span className="caption-muted">{t("nat.sources")}</span></div>
        </div>

        <LiveStrip now={now.data} failed={now.isError} lang={lang} />

        <section className="stack">
          <h3>{t("nat.prodcons")}</h3>
          <Legend items={[{ color: "var(--data-production)", label: t("nat.production") }, { color: "var(--data-consumption)", label: t("nat.consumption") }]} />
          <ChartOrTable
            chart={<Chart ariaLabel={t("nat.prodcons")} height={220} deps={[N]} build={(tk, _w, fm) => prodConsOption(tk, N, t, fm)} />}
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
          <Chart ariaLabel={t("nat.mix")} height={200} deps={[N]} build={(tk, _w, fm) => mixOption(tk, N, t, fm)} />
        </section>

        <section className="stack">
          <h3>{t("nat.neg")}</h3>
          <Chart ariaLabel={t("nat.neg")} height={150} deps={[N]} build={(tk, _w, fm) => negOption(tk, N, fm)} />
        </section>

        <section className="stack">
          <h3>{t("nat.capTitle")}</h3>
          <p className="caption">{t("nat.capText")}</p>
          <ul className="list">
            <li><a href="https://www.iea.org/reports/electricity-2026/grids" target="_blank" rel="noreferrer">{t("nat.iea")}</a></li>
            {N.capacity_links.map((l: any) => (
              <li key={l.url}><a href={l.url} target="_blank" rel="noreferrer">{l.name}</a> <Chip kind="reference" /></li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}

/** Right now in the site's bidding zone: this quarter hour's spot price, today's range, the air at Bromma. */
function LiveStrip({ now, failed, lang }: { now: any; failed: boolean; lang: string }) {
  const t = useT();
  const f = useFmt();
  if (!now && !failed) return null;
  const cur = now?.current;
  const range = now?.today_range;
  const temp = now?.temperature;
  const clock = (iso: string) => new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  if (failed || (!cur && !temp)) {
    return <p className="caption-muted live-strip">{t("nat.live.off")}</p>;
  }
  return (
    <section className="live-strip" aria-label={t("nat.live")}>
      <h3>{t("nat.live")} <span className="live-dot" aria-hidden="true" /></h3>
      <dl className="kv">
        {cur && <><dt>{t("nat.live.price", { at: clock(cur.start) })}</dt><dd>{f(cur.sek_kwh, 2)} SEK/kWh</dd></>}
        {range && <><dt>{t("nat.live.range")}</dt><dd>{f(range.min_sek_kwh, 2)}–{f(range.max_sek_kwh, 2)} SEK/kWh</dd></>}
        {temp && <><dt>{t("nat.live.temp", { station: temp.station || "SMHI", at: clock(temp.time) })}</dt><dd>{f(temp.value_c, 1)} °C</dd></>}
      </dl>
      <p className="caption-muted">{now.price_attribution} · SMHI (CC BY)</p>
    </section>
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

function prodConsOption(tk: Tokens, N: any, t: (k: string) => string, f: (v: number | null | undefined, d?: number) => string) {
  return {
    grid: { left: 40, right: 12, top: 12, bottom: 26 },
    xAxis: { type: "category", data: ZONES, ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(tk), name: "TWh" },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => `${f(v, 1)} TWh` },
    series: [
      { type: "bar", name: t("nat.production"), data: ZONES.map((z) => round(N.zones[z].production_twh, 1)), itemStyle: { color: tk.production, borderRadius: [tk.size.radius, tk.size.radius, 0, 0] }, barWidth: 20, barGap: "10%" },
      { type: "bar", name: t("nat.consumption"), data: ZONES.map((z) => round(N.zones[z].consumption_twh, 1)), itemStyle: { color: tk.consumption, borderRadius: [tk.size.radius, tk.size.radius, 0, 0] }, barWidth: 20 },
    ],
  };
}

function mixOption(tk: Tokens, N: any, t: (k: string) => string, f: (v: number | null | undefined, d?: number) => string) {
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
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => `${f(v, 1)} TWh` },
    series: TYPES.map((ty, i) => ({
      type: "bar", stack: "mix", name: t(`type.${ty}`), barWidth: 18,
      data: ZONES.map((z) => round(val(z, ty), 2)),
      itemStyle: { color: colors[i], borderColor: tk.surface, borderWidth: 1 },
    })),
  };
}

function negOption(tk: Tokens, N: any, f: (v: number | null | undefined, d?: number) => string) {
  return {
    grid: { left: 40, right: 40, top: 6, bottom: 22 },
    xAxis: { type: "value", ...axisStyle(tk) },
    yAxis: { type: "category", data: ZONES, inverse: true, ...axisStyle(tk), splitLine: { show: false } },
    tooltip: { trigger: "item", formatter: (p: any) => `${p.name}: <b>${f(p.value)}</b> h` },
    series: [{
      type: "bar", barWidth: 14, data: ZONES.map((z) => N.zones[z].negative_price_hours),
      itemStyle: { color: tk.main, borderRadius: [0, tk.size.radius, tk.size.radius, 0] },
      label: { show: true, position: "right", color: tk.text, formatter: (p: any) => `${f(p.value)} h` },
    }],
  };
}

