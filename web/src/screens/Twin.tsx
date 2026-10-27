import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { MapView, fitTo } from "../components/MapView";
import { DataTable, type Col } from "../components/DataTable";
import { YearStrip } from "../components/YearStrip";
import { SplitSkeleton } from "../components/Skeleton";
import { Chip, Meter, Status, Tile, Missing } from "../components/bits";
import { Icon } from "../components/Icon";
import { useApi, useGridState } from "../lib/api";
import { loadingColor, powerColor, tokens, voltageColor } from "../lib/colors";
import { hourLabel, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { SCENARIOS, useStore } from "../lib/store";
import { nightOpacity, solarElevation } from "../lib/sun";

type ColorBy = "voltage" | "power";
/** Colour scales saturate here: ±6 % voltage (limits are ±10 %) and ±8 kW net power. */
const V_SPAN = 0.06;
/** The policy the published per-customer figures use (engine/run.py REF_POLICY). */
const REF_POLICY = "maxmin";

/** One house as the table sees it: what the network says, plus this hour's state. */
interface HouseRow {
  cid: number;
  address: string;
  feeder: number;
  status: "firm" | "nonfirm" | "none";
  kwp: number;
  fuse: number;
  dist: number;
  v: number | null;
  p: number | null;
  curtailed: number | null;
}
const P_SPAN = 8;

export default function Twin() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const hour = useStore((s) => s.hour);
  const setHour = useStore((s) => s.setHour);
  const scenario = useStore((s) => s.scenario);
  const setScenario = useStore((s) => s.setScenario);
  const playing = useStore((s) => s.playing);
  const setPlaying = useStore((s) => s.setPlaying);
  const theme = useStore((s) => s.theme);

  const meta = useApi<any>("/meta");
  const net = useApi<any>("/network");
  const year = meta.data?.reference_year ?? 2025;
  const series = useApi<any>(meta.data ? `/series/${year}` : null);
  const show = useApi<any>("/showcase");
  const st = useGridState(scenario);
  const mapRef = useRef<MLMap | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; kind: string; id: number } | null>(null);
  const [colorBy, setColorBy] = useState<ColorBy>("voltage");
  const [speed, setSpeed] = useState(1);
  const [openTable, setOpenTable] = useState(false);
  const selectedCid = useStore((s) => s.selectedCid);
  const select = useStore((s) => s.select);
  // setup/onReady run from map events, so they call the latest paint through this ref
  const paintRef = useRef<(map: MLMap | null) => void>(() => {});

  const houses = useMemo(() => net.data?.features.filter((x: any) => x.properties.layer === "house") ?? [], [net.data]);
  const cables = useMemo(() => net.data?.features.filter((x: any) => x.properties.layer === "cable") ?? [], [net.data]);

  const picked = useMemo(
    () => (selectedCid == null ? null : houses.find((h: any) => h.properties.cid === selectedCid) ?? null),
    [houses, selectedCid],
  );

  // bus / line → row in the state arrays
  const rows = useMemo(() => {
    if (!st.data) return null;
    const busRow = new Map<number, number>();
    st.data.header.bus_index.forEach((b, i) => busRow.set(b, i));
    const lineRow = new Map<number, number>();
    st.data.header.line_index.forEach((l, i) => lineRow.set(l, i));
    return { busRow, lineRow };
  }, [st.data]);

  // ------------------------------------------------------------------ map layers
  const setup = useCallback(
    (map: MLMap) => {
      if (!net.data) return;
      const tk = tokens();
      const fc = (features: any[]) => ({ type: "FeatureCollection", features });
      const ctx = net.data.features.filter((x: any) => x.properties.layer === "context");
      const cab = net.data.features.filter((x: any) => x.properties.layer === "cabinet");
      const sub = net.data.features.filter((x: any) => x.properties.layer === "substation");
      const hs = houses.map((h: any) => ({ ...h, properties: { ...h.properties, height: Math.max(1, h.properties.levels) * 3.2 } }));
      const add = (id: string, data: any, promoteId?: string) => {
        if (map.getSource(id)) return;
        map.addSource(id, { type: "geojson", data, ...(promoteId ? { promoteId } : {}) } as any);
      };
      add("context", fc(ctx));
      add("cables", fc(cables), "line");
      add("houses", fc(hs), "cid");
      add("cabinets", fc(cab));
      add("substation", fc(sub));
      if (!map.getLayer("context")) {
        map.addLayer({ id: "context", type: "fill", source: "context", paint: { "fill-color": tk.border, "fill-opacity": 0.7 } });
        map.addLayer({
          id: "cables", type: "line", source: "cables",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ["coalesce", ["feature-state", "color"], tk.load[2]],
            // zoom must be the top-level input of interpolate; the cable kind is decided inside
            "line-width": ["interpolate", ["linear"], ["zoom"],
              14, ["case", ["==", ["get", "kind"], "main"], 2, 0.8],
              18, ["case", ["==", ["get", "kind"], "main"], 5.5, 2]],
          },
        });
        map.addLayer({
          id: "houses", type: "fill-extrusion", source: "houses",
          paint: {
            "fill-extrusion-color": ["coalesce", ["feature-state", "color"], tk.voltMid],
            "fill-extrusion-height": ["get", "height"],
            "fill-extrusion-opacity": 0.92,
          },
        });
        map.addLayer({
          id: "houses-sel", type: "line", source: "houses",
          paint: { "line-color": tk.text, "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0] },
        });
        map.addLayer({ id: "cabinets", type: "circle", source: "cabinets", paint: { "circle-radius": 2.5, "circle-color": tk.text2, "circle-stroke-width": 1.5, "circle-stroke-color": tk.surface } });
        map.addLayer({ id: "substation", type: "circle", source: "substation", paint: { "circle-radius": 8, "circle-color": tk.text, "circle-stroke-width": 3, "circle-stroke-color": tk.surface } });
      }
      paintRef.current(map);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [net.data, theme],
  );

  /** One row per house, carrying this hour's voltage and net power from the loaded state. */
  const tableRows: HouseRow[] = useMemo(() => {
    const per = show.data?.data?.showcase?.policies?.[REF_POLICY]?.per_customer;
    const cut = new Map<number, number>();
    if (per) per.cid.forEach((c: number, i: number) => cut.set(c, per.curtailed_kwh[i]));
    return houses.map((h: any) => {
      const p = h.properties;
      const r = rows?.busRow.get(p.bus);
      return {
        cid: p.cid as number,
        address: (p.address as string) || t("dt.unnamed", { id: p.cid }),
        feeder: p.feeder as number,
        status: p.pv_status as "firm" | "nonfirm" | "none",
        kwp: p.kwp as number,
        fuse: p.fuse_a as number,
        dist: p.dist_m as number,
        v: st.data && r !== undefined ? st.data.voltage(r, hour) : null,
        p: st.data ? st.data.power(p.cid, hour) : null,
        curtailed: cut.get(p.cid) ?? null,
      };
    });
  }, [houses, rows, st.data, hour, show.data, t]);

  const houseCols: Col<HouseRow>[] = useMemo(
    () => [
      { key: "address", label: t("dt.address"), value: (r) => r.address },
      { key: "feeder", label: t("dt.feeder"), value: (r) => r.feeder, numeric: true },
      {
        key: "status",
        label: t("dt.status"),
        value: (r) => t(`twin.status.${r.status === "none" ? "noPv" : r.status}`),
        // a house without solar is not a warning, it is simply not in this study, so it
        // gets no status colour at all (§1.4: colour here means something)
        cell: (r) =>
          r.status === "none" ? (
            <span className="caption-muted">{t("twin.status.noPv")}</span>
          ) : (
            <Status kind={r.status === "firm" ? "good" : "warning"}>{t(`twin.status.${r.status}`)}</Status>
          ),
      },
      { key: "kwp", label: t("dt.kwp"), unit: "kWp", value: (r) => r.kwp, cell: (r) => f(r.kwp, 1), numeric: true },
      { key: "fuse", label: t("dt.fuse"), unit: "A", value: (r) => r.fuse, numeric: true },
      { key: "dist", label: t("dt.dist"), unit: "m", value: (r) => r.dist, cell: (r) => f(r.dist, 0), numeric: true },
      {
        key: "v",
        label: t("dt.voltage"),
        unit: "pu",
        value: (r) => r.v,
        cell: (r) => (r.v == null ? "–" : <span style={{ color: voltageColor(tokens(), r.v, V_SPAN) }}>{f(r.v, 3)}</span>),
        numeric: true,
      },
      { key: "p", label: t("dt.net"), unit: "kW", value: (r) => r.p, cell: (r) => (r.p == null ? "–" : f(r.p, 1)), numeric: true },
      {
        key: "curtailed",
        label: t("dt.curtailed"),
        unit: "kWh",
        value: (r) => r.curtailed,
        cell: (r) => (r.curtailed == null ? "–" : f(r.curtailed, 0)),
        numeric: true,
      },
    ],
    [t, f],
  );

  const pickedNow = useMemo(() => tableRows.find((r) => r.cid === selectedCid) ?? null, [tableRows, selectedCid]);

  const paint = useCallback(
    (map: MLMap | null) => {
      if (!map || !st.data || !rows || !map.getSource("houses")) return;
      const tk = tokens();
      const h = hour;
      for (const hs of houses) {
        const r = rows.busRow.get(hs.properties.bus);
        if (r === undefined) continue;
        const color = colorBy === "voltage"
          ? voltageColor(tk, st.data.voltage(r, h), V_SPAN)
          : powerColor(tk, st.data.power(hs.properties.cid, h), P_SPAN);
        map.setFeatureState({ source: "houses", id: hs.properties.cid }, { color });
      }
      for (const c of cables) {
        const r = rows.lineRow.get(c.properties.line);
        if (r === undefined) continue;
        map.setFeatureState({ source: "cables", id: c.properties.line }, { color: loadingColor(tk, st.data.loading(r, h)) });
      }
      // the substation marker turns critical when the transformer is above its rating
      if (map.getLayer("substation")) {
        const over = st.data.header.aggregates.trafo_loading[h] > 100;
        map.setPaintProperty("substation", "circle-color", over ? tk.danger : tk.text);
        map.setPaintProperty("substation", "circle-radius", over ? 11 : 8);
      }
    },
    [st.data, rows, hour, houses, cables, colorBy],
  );

  paintRef.current = paint;
  useEffect(() => paint(mapRef.current), [paint]);

  // hover tooltip
  const onReady = useCallback((map: MLMap) => {
    mapRef.current = map;
    if (net.data?.bbox) fitTo(map, net.data.bbox, 60);
    // the map could hover a house but never select one: the table, the panel and the
    // map all read the same selectedCid now, so picking in any of them moves the others
    map.on("click", "houses", (e: MapMouseEvent & { features?: any[] }) => {
      const cid = e.features?.[0]?.properties?.cid;
      if (cid != null) select(Number(cid));
    });
    let hovered: number | null = null;
    map.on("mousemove", (e: MapMouseEvent) => {
      const feats = map.queryRenderedFeatures(e.point, { layers: ["houses", "cables"] });
      if (hovered !== null) map.setFeatureState({ source: "houses", id: hovered }, { hover: false });
      hovered = null;
      if (!feats.length) {
        setTip(null);
        map.getCanvas().style.cursor = "";
        return;
      }
      const ft = feats[0];
      map.getCanvas().style.cursor = "pointer";
      if (ft.layer.id === "houses") {
        hovered = ft.properties.cid as number;
        map.setFeatureState({ source: "houses", id: hovered }, { hover: true });
        setTip({ x: e.point.x, y: e.point.y, kind: "house", id: hovered });
      } else {
        setTip({ x: e.point.x, y: e.point.y, kind: "cable", id: ft.properties.line as number });
      }
    });
    map.on("mouseout", () => setTip(null));
    paintRef.current(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net.data]);

  // open on the most loaded hour of the year unless the viewer has already picked one
  useEffect(() => {
    const h = meta.data?.showcase_hour;
    if (typeof h === "number" && !useStore.getState().hourTouched) useStore.setState({ hour: h });
  }, [meta.data]);

  // playback: one hour per frame tick
  useEffect(() => {
    if (!playing) return;
    const H = st.data?.header.hours ?? 8760;
    const id = window.setInterval(() => {
      const cur = useStore.getState().hour;
      setHour((cur + 1) % H);
    }, 600 / speed);
    return () => window.clearInterval(id);
  }, [playing, st.data, setHour, speed]);

  useEffect(() => () => setPlaying(false), [setPlaying]);

  if (net.isError || meta.isError || st.isError) return <Missing of={[net, meta, st]} />;
  if (!net.data || !meta.data) return <SplitSkeleton />;

  const sc = show.data?.data?.showcase;
  const agg = st.isPlaceholderData ? undefined : st.data?.header.aggregates;
  const center: [number, number] = meta.data.center;
  const startUtc = Date.UTC(year, 0, 1) - 3600_000; // UTC+1 standard time year start
  const elev = solarElevation(startUtc + (hour + 0.5) * 3600_000, center[1], center[0]);
  const night = nightOpacity(elev);
  const ser = series.data?.data;
  const nNonfirm = sc ? sc.nonfirm_cids.length : 0;
  const scnText = sc
    ? scenario === "static"
      ? t("scn.static.d", { n: sc.firm })
      : scenario === "uncontrolled"
        ? t("scn.uncontrolled.d", { n: sc.adopters, h: sc.verification.uncontrolled_violation_hours, year })
        : t("scn.doe.d", { n: sc.adopters, k: nNonfirm })
    : "";
  const violation = agg ? agg.violation[hour] === 1 : false;
  const trafoRev = agg ? agg.trafo_p_kw[hour] < 0 : false;

  return (
    <div className="split">
      <div className="map-col">
        <div className="map-wrap">
          <MapView center={center} zoom={16} pitch={50} bearing={-18} setup={setup} onReady={onReady} ariaLabel={t("twin.title", { n: houses.length })}>
            <div className="daylight" style={{ backgroundColor: `rgba(var(--night), ${night})` }} />
            {(st.isLoading || st.isPlaceholderData) && <div className="map-loading" role="status">{t("ui.loading")}</div>}
            <div className="map-toolbar"><button className="icon-btn" aria-label={t("twin.resetView")} title={t("twin.resetView")} onClick={() => { if (mapRef.current && net.data.bbox) { mapRef.current.jumpTo({ pitch: 50, bearing: -18 }); fitTo(mapRef.current, net.data.bbox, 60); } }}><Icon name="reset" size={17} /></button></div>
            <MapLegend colorBy={colorBy} />
            <div className="map-note">
              <Chip kind="real" />
              
              <Chip kind="synthetic" />
            </div>
            {tip && st.data && rows && <Tooltip tip={tip} houses={houses} cables={cables} rows={rows} hour={hour} />}
          </MapView>
        </div>
        <div className="yearstrip">
          <div className="yearstrip-controls">
            <span className="eyebrow">{t("twin.timeline")}</span>
            <span className="yearstrip-when">{hourLabel(lang, year, hour)}</span>
            <div className="timeline-buttons">
              <button className="icon-btn" title={t("twin.previous")} aria-label={t("twin.previous")} disabled={!agg || hour === 0} onClick={() => { setPlaying(false); setHour(Math.max(0, hour - 1)); }}><Icon name="previous" size={17} /></button>
              <button className="icon-btn play-button" disabled={!agg} onClick={() => setPlaying(!playing)} aria-pressed={playing} aria-label={playing ? t("twin.pause") : t("twin.play")}><Icon name={playing ? "pause" : "play"} size={16} />{playing ? t("twin.pause") : t("twin.play")}</button>
              <button className="icon-btn" title={t("twin.next")} aria-label={t("twin.next")} disabled={!agg || hour >= (st.data?.header.hours ?? 8760) - 1} onClick={() => { setPlaying(false); setHour(Math.min((st.data?.header.hours ?? 8760) - 1, hour + 1)); }}><Icon name="next" size={17} /></button>
            </div>
            <div className="timeline-tools"><select className="theme-select" aria-label={t("twin.speed")} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[1, 4, 8].map((s) => <option key={s} value={s}>{s}×</option>)}</select><button className="icon-btn" disabled={typeof meta.data.showcase_hour !== "number"} onClick={() => { setPlaying(false); setHour(meta.data.showcase_hour); }}>{t("twin.peak")}</button></div>
          </div>
          {agg ? (
            <YearStrip values={agg.trafo_loading} hours={st.data!.header.hours} label={t("twin.strip", { year })} />
          ) : (
            <div className="caption">{t(st.isError ? "ui.unavailable" : "ui.loading")}</div>
          )}
          <div />
          <div className="timeline-caption"><input className="timeline-scrubber" type="range" min={0} max={(st.data?.header.hours ?? 8760) - 1} value={hour} disabled={!agg} aria-label={t("twin.hour")} aria-valuetext={hourLabel(lang, year, hour)} onChange={(e) => { setPlaying(false); setHour(Number(e.target.value)); }} /><p className="caption-muted">{t("twin.strip", { year })}</p></div>
        </div>
        <div className={`houses-region ${openTable ? "open" : ""}`}>
          <button className="region-toggle" aria-expanded={openTable} onClick={() => setOpenTable(!openTable)}>
            <Icon name={openTable ? "sort" : "next"} size={15} />
            <span>{t("dt.houses", { n: houses.length })}</span>
            <span className="caption-muted">{t("dt.hint")}</span>
          </button>
          {openTable && (
            <DataTable
              rows={tableRows}
              cols={houseCols}
              rowKey={(r) => r.cid}
              caption={t("dt.caption", { n: houses.length, when: hourLabel(lang, year, hour) })}
              searchLabel={t("dt.searchHouses")}
              selected={selectedCid}
              onSelect={(r) => select(r.cid)}
              initialSort="address"
            />
          )}
        </div>
      </div>

      <aside className="panel">
        {picked && (
          <div className="stack inspector">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>{picked.properties.address || t("dt.unnamed", { id: picked.properties.cid })}</h3>
              <button className="icon-btn" aria-label={t("twin.clear")} title={t("twin.clear")} onClick={() => select(null)}>
                <Icon name="close" size={15} />
              </button>
            </div>
            <div className="tiles">
              <Tile label={t("dt.voltage")} value={pickedNow ? f(pickedNow.v, 3) : "–"} unit="pu" />
              <Tile label={t("dt.net")} value={pickedNow ? f(pickedNow.p, 1) : "–"} unit="kW" />
            </div>
            <dl className="kv">
              <dt>{t("dt.status")}</dt>
              <dd>{t(`twin.status.${picked.properties.pv_status === "none" ? "noPv" : picked.properties.pv_status}`)}</dd>
              <dt>{t("dt.feeder")}</dt>
              <dd>{picked.properties.feeder}</dd>
              <dt>{t("dt.kwp")}</dt>
              <dd>{f(picked.properties.kwp, 1)} kWp</dd>
              <dt>{t("dt.fuse")}</dt>
              <dd>{picked.properties.fuse_a} A</dd>
              <dt>{t("dt.dist")}</dt>
              <dd>{f(picked.properties.dist_m, 0)} m</dd>
            </dl>
          </div>
        )}
        <div className="stack">
          <h2>{t("twin.title", { n: houses.length })}</h2>
          <p className="caption">{t("twin.sub", { addr: meta.data.substation?.address ?? "" })}</p>
        </div>

        <div className="stack">
          <div className="seg" role="group" aria-label={t("twin.scenario")}>
            {SCENARIOS.map((s) => (
              <button key={s} aria-pressed={scenario === s} onClick={() => setScenario(s)}>
                {t(`scn.${s}`)}
              </button>
            ))}
          </div>
          <p className="caption">{scnText}</p>
          <div className="row">
            <span className="caption">{t("twin.colorBy")}</span>
            <div className="seg small" role="group" aria-label={t("twin.colorBy")}>
              {(["voltage", "power"] as ColorBy[]).map((c) => (
                <button key={c} aria-pressed={colorBy === c} onClick={() => setColorBy(c)}>
                  {t(c === "voltage" ? "twin.byVoltage" : "twin.byPower")}
                </button>
              ))}
            </div>
          </div>
        </div>

        <hr className="divider" />

        <div className="stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>{hourLabel(lang, year, hour)}</h3>
            {agg && (violation ? <Status kind="critical">{t("ui.exceeded")}</Status> : <Status kind="good">{t("ui.within")}</Status>)}
          </div>
          <div className="tiles">
            <Tile label={t("twin.sun")} value={f(elev, 0)} unit="°" />
            <Tile label={t("twin.ghi")} value={ser ? f(ser.ghi[hour], 0) : "–"} unit="W/m²" note={<Chip kind="real" />} />
            <Tile label={t("twin.temp")} value={ser ? f(ser.t_air[hour], 1) : "–"} unit="°C" note={<Chip kind="real" />} />
            <Tile label={t("twin.price")} value={ser?.price_sek_kwh ? f(ser.price_sek_kwh[hour], 2) : "–"} unit="kr/kWh" note={<Chip kind="real" />} />
            <Tile label={t("twin.pv")} value={agg ? f(agg.pv_kw[hour], 0) : "–"} unit="kW" />
            <Tile label={t("twin.load")} value={agg ? f(agg.load_kw[hour], 0) : "–"} unit="kW" />
          </div>
        </div>

        {agg && (
          <div className="stack">
            <Meter label={`${t("twin.trafo")}${trafoRev ? " ⇡" : ""}`} value={agg.trafo_loading[hour]} max={140} limit={100} />
            <Meter label={t("twin.line")} value={agg.max_line_loading[hour]} max={140} limit={100} />
            <dl className="kv">
              <dt>{t("twin.vmax")}</dt>
              <dd>{f(agg.vmax[hour], 3)} pu</dd>
              <dt>{t("twin.vmin")}</dt>
              <dd>{f(agg.vmin[hour], 3)} pu</dd>
              {scenario === "doe" && agg.curtailed_kw && (
                <>
                  <dt>{t("twin.curtailed")}</dt>
                  <dd>{f(agg.curtailed_kw[hour], 1)} kW</dd>
                </>
              )}
            </dl>
          </div>
        )}
      </aside>
    </div>
  );
}

function MapLegend({ colorBy }: { colorBy: ColorBy }) {
  const t = useT();
  const f = useFmt();
  const tk = tokens();
  const theme = useStore((s) => s.theme);
  void theme;
  const seqGrad = `linear-gradient(90deg, ${tk.load.join(",")})`;
  // power is drawn on the voltage poles (powerColor), so both scales share one bar
  const divGrad = `linear-gradient(90deg, ${tk.voltLow}, ${tk.voltMid}, ${tk.voltHigh})`;
  return (
    <div className="map-legend">
      <div className="scale">
        <span>{t("twin.legend.cable")}</span>
        <div className="scale-bar" style={{ background: seqGrad }} />
        <div className="scale-ticks">
          <span>0 %</span>
          <span>50 %</span>
          <span>100 %</span>
        </div>
        <span className="row" style={{ gap: 6 }}>
          <i style={{ display: "inline-block", width: 12, height: 4, borderRadius: 2, background: tk.danger }} />
          {t("twin.legend.over")}
        </span>
      </div>
      {colorBy === "voltage" ? (
        <div className="scale">
          <span>{t("twin.legend.voltage")}</span>
          <div className="scale-bar" style={{ background: divGrad }} />
          <div className="scale-ticks">
            <span>≤{f(1 - V_SPAN, 2)}</span>
            <span>{f(1, 2)}</span>
            <span>≥{f(1 + V_SPAN, 2)}</span>
          </div>
          <span className="caption-muted">{t("twin.legend.vnote", { p: f(V_SPAN * 100, 0) })}</span>
        </div>
      ) : (
        <div className="scale">
          <span>{t("twin.legend.power")}</span>
          <div className="scale-bar" style={{ background: divGrad }} />
          <div className="scale-ticks">
            <span>{P_SPAN} {t("twin.legend.import")}</span>
            <span>0</span>
            <span>{P_SPAN} {t("twin.legend.export")}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Tooltip({ tip, houses, cables, rows, hour }: {
  tip: { x: number; y: number; kind: string; id: number };
  houses: any[]; cables: any[];
  rows: { busRow: Map<number, number>; lineRow: Map<number, number> };
  hour: number;
}) {
  const t = useT();
  const f = useFmt();
  const scenario = useStore((s) => s.scenario);
  const st = useGridState(scenario);
  if (!st.data) return null;
  const style = { left: tip.x + 14, top: tip.y + 14 };
  if (tip.kind === "house") {
    const h = houses.find((x) => x.properties.cid === tip.id)?.properties;
    if (!h) return null;
    const r = rows.busRow.get(h.bus);
    const v = r !== undefined ? st.data.voltage(r, hour) : NaN;
    const p = st.data.power(h.cid, hour);
    return (
      <div className="map-tooltip" style={style}>
        <div className="tt-title">{h.address || h.osm_id}</div>
        <div className="tt-row"><span>{t("twin.tt.net")}</span><b>{f(Math.abs(p), 1)} kW {p >= 0 ? t("twin.tt.export") : t("twin.tt.import")}</b></div>
        <div className="tt-row"><span>{t("twin.tt.voltage")}</span><b>{f(v, 3)} pu</b></div>
        <div className="tt-row"><span>{t("twin.tt.status")}</span><b>{t(`twin.status.${h.pv_status}`)}</b></div>
        <div className="tt-row"><span>{t("twin.tt.pv")}</span><b>{f(h.kwp, 1)} kWp</b></div>
        <div className="tt-row"><span>{t("twin.tt.fuse")}</span><b>{h.fuse_a} A</b></div>
      </div>
    );
  }
  const c = cables.find((x) => x.properties.line === tip.id)?.properties;
  if (!c) return null;
  const r = rows.lineRow.get(c.line);
  const l = r !== undefined ? st.data.loading(r, hour) : NaN;
  return (
    <div className="map-tooltip" style={style}>
      <div className="tt-title">{c.kind === "main" ? t("twin.feeder", { n: c.feeder + 1 }) : t("twin.service")}</div>
      <div className="tt-row"><span>{t("twin.tt.loading")}</span><b>{f(l, 0)} %</b></div>
      <div className="tt-row"><span>{t("twin.type")}</span><b>{c.std_type}{c.parallel > 1 ? ` ×${c.parallel}` : ""}</b></div>
      <div className="tt-row"><span>{t("twin.length")}</span><b>{f(c.length_m, 0)} m</b></div>
    </div>
  );
}
