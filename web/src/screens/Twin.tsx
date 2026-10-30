import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type MutableRefObject } from "react";
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { MapView, fitTo } from "../components/MapView";
import { DataTable, type Col } from "../components/DataTable";
import { YearStrip } from "../components/YearStrip";
import { SplitSkeleton } from "../components/Skeleton";
import { Meter, Status, Tile, Missing } from "../components/bits";
import { Icon } from "../components/Icon";
import { Term } from "../components/Term";
import { useApi, useGridState } from "../lib/api";
import { loadingColor, powerColor, rampCss, tokens, voltageColor } from "../lib/colors";
import { hourLabel, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { SCENARIOS, useStore } from "../lib/store";
import { nightOpacity, solarElevation } from "../lib/sun";

type ColorBy = "voltage" | "power";
/** Colour scales saturate here: ±6 % voltage (limits are ±10 %) and ±8 kW net power. */
const V_SPAN = 0.06;
const P_SPAN = 8;
/** The policy the published per-customer figures use (engine/run.py REF_POLICY). */
const REF_POLICY = "maxmin";

type GridState = ReturnType<typeof useGridState>;
type Rows = { busRow: Map<number, number>; lineRow: Map<number, number> };
type Tip = { x: number; y: number; kind: "house" | "cable"; id: number };

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

const rowKey = (r: HouseRow) => r.cid;

/** Start of the study year in UTC: the year runs in UTC+1 standard time. */
const yearStart = (year: number) => Date.UTC(year, 0, 1) - 3600_000;

/**
 * The Neighbourhood sheet. The hour changes up to 13 times a second during playback, so
 * nothing here reads it: the map is painted from a store subscription, and the parts that
 * show the hour (daylight, timeline, table, inspector, the hour's readings) each subscribe
 * on their own (rerender-defer-reads).
 */
export default function Twin() {
  const t = useT();
  const scenario = useStore((s) => s.scenario);
  const setScenario = useStore((s) => s.setScenario);
  const theme = useStore((s) => s.theme);
  const selectedCid = useStore((s) => s.selectedCid);
  const select = useStore((s) => s.select);
  const playing = useStore((s) => s.playing);

  const meta = useApi<any>("/meta");
  const net = useApi<any>("/network");
  const year = meta.data?.reference_year ?? 2025;
  const series = useApi<any>(meta.data ? `/series/${year}` : null);
  const show = useApi<any>("/showcase");
  const st = useGridState(scenario);
  const mapRef = useRef<MLMap | null>(null);
  const tipRef = useRef<(tip: Tip | null) => void>(() => {});
  const lastColor = useRef(new Map<string, string>());
  const [colorBy, setColorBy] = useState<ColorBy>("voltage");
  const [openTable, setOpenTable] = useState(false);
  const colRef = useRef<HTMLDivElement>(null);

  // the open table is taller than what is left under the map on a laptop screen, so the
  // column scrolls to show all of it; the column itself, never scrollIntoView, which would
  // move every scroller up to the window
  useEffect(() => {
    const col = colRef.current;
    if (!openTable || !col) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    col.scrollTo({ top: col.scrollHeight, behavior: still ? "auto" : "smooth" });
  }, [openTable]);
  // setup/onReady run from map events, so they call the latest paint through this ref
  const paintRef = useRef<(map: MLMap | null) => void>(() => {});

  const houses = useMemo(() => net.data?.features.filter((x: any) => x.properties.layer === "house") ?? [], [net.data]);
  const cables = useMemo(() => net.data?.features.filter((x: any) => x.properties.layer === "cable") ?? [], [net.data]);
  // the tooltip looks a feature up on every hover: by id, not by scanning (js-index-maps)
  const housesById = useMemo(() => new Map<number, any>(houses.map((h: any) => [h.properties.cid, h.properties])), [houses]);
  const cablesById = useMemo(() => new Map<number, any>(cables.map((c: any) => [c.properties.line, c.properties])), [cables]);
  const picked = selectedCid == null ? null : housesById.get(selectedCid) ?? null;

  const curtailed = useMemo(() => {
    const per = show.data?.data?.showcase?.policies?.[REF_POLICY]?.per_customer;
    const cut = new Map<number, number>();
    if (per) per.cid.forEach((c: number, i: number) => cut.set(c, per.curtailed_kwh[i]));
    return cut;
  }, [show.data]);

  // bus / line → row in the state arrays
  const rows: Rows | null = useMemo(() => {
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
        map.addLayer({ id: "context", type: "fill", source: "context", paint: { "fill-color": tk.text2, "fill-opacity": 0.1 } });
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
      // a new style has no feature state: every colour has to be set again
      lastColor.current.clear();
      paintRef.current(map);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [net.data, theme],
  );

  /** Colours the houses, cables and substation for the hour in the store, changing only what changed. */
  const paint = useCallback(
    (map: MLMap | null) => {
      if (!map || !st.data || !rows || !map.getSource("houses")) return;
      const tk = tokens();
      const h = useStore.getState().hour;
      const last = lastColor.current;
      const put = (source: string, id: number, color: string) => {
        const key = `${source}:${id}`;
        if (last.get(key) === color) return;
        last.set(key, color);
        map.setFeatureState({ source, id }, { color });
      };
      for (const hs of houses) {
        const r = rows.busRow.get(hs.properties.bus);
        if (r === undefined) continue;
        put("houses", hs.properties.cid, colorBy === "voltage"
          ? voltageColor(tk, st.data.voltage(r, h), V_SPAN)
          : powerColor(tk, st.data.power(hs.properties.cid, h), P_SPAN));
      }
      for (const c of cables) {
        const r = rows.lineRow.get(c.properties.line);
        if (r === undefined) continue;
        put("cables", c.properties.line, loadingColor(tk, st.data.loading(r, h)));
      }
      // the substation marker turns critical when the transformer is above its rating
      if (map.getLayer("substation")) {
        const over = st.data.header.aggregates.trafo_loading[h] > 100;
        map.setPaintProperty("substation", "circle-color", over ? tk.danger : tk.text);
        map.setPaintProperty("substation", "circle-radius", over ? 11 : 8);
      }
    },
    [st.data, rows, houses, cables, colorBy],
  );

  paintRef.current = paint;
  useEffect(() => {
    // a new state or colouring repaints everything
    lastColor.current.clear();
    paint(mapRef.current);
  }, [paint]);

  // the hour repaints outside React, at most once per frame
  useEffect(() => {
    let frame = 0;
    const unsubscribe = useStore.subscribe((s, prev) => {
      if (s.hour === prev.hour || frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        paintRef.current(mapRef.current);
      });
    });
    return () => {
      unsubscribe();
      cancelAnimationFrame(frame);
    };
  }, []);

  const onReady = useCallback((map: MLMap) => {
    mapRef.current = map;
    if (net.data?.bbox) fitTo(map, net.data.bbox, 60);
    // the map could hover a house but never select one: the table, the panel and the
    // map all read the same selectedCid now, so picking in any of them moves the others
    map.on("click", "houses", (e: MapMouseEvent & { features?: any[] }) => {
      const cid = e.features?.[0]?.properties?.cid;
      if (cid != null) select(Number(cid));
    });
    // hover is read once per frame, and touches feature state only when the house changes
    let hovered: number | null = null;
    let point: { x: number; y: number } | null = null;
    let frame = 0;
    const read = () => {
      frame = 0;
      if (!point) return;
      const feats = map.queryRenderedFeatures([point.x, point.y], { layers: ["houses", "cables"] });
      const ft = feats[0];
      const house = ft?.layer.id === "houses" ? (ft.properties.cid as number) : null;
      if (house !== hovered) {
        if (hovered !== null) map.setFeatureState({ source: "houses", id: hovered }, { hover: false });
        if (house !== null) map.setFeatureState({ source: "houses", id: house }, { hover: true });
        hovered = house;
      }
      map.getCanvas().style.cursor = ft ? "pointer" : "";
      if (!ft) tipRef.current(null);
      else if (house !== null) tipRef.current({ x: point.x, y: point.y, kind: "house", id: house });
      else tipRef.current({ x: point.x, y: point.y, kind: "cable", id: ft.properties.line as number });
    };
    map.on("mousemove", (e: MapMouseEvent) => {
      point = { x: e.point.x, y: e.point.y };
      if (!frame) frame = requestAnimationFrame(read);
    });
    map.on("mouseout", () => {
      point = null;
      tipRef.current(null);
    });
    paintRef.current(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net.data]);

  // open on the most loaded hour of the year unless the viewer has already picked one
  useEffect(() => {
    const h = meta.data?.showcase_hour;
    if (typeof h === "number" && !useStore.getState().hourTouched) useStore.setState({ hour: h });
  }, [meta.data]);

  if (net.isError || meta.isError || st.isError) return <Missing of={[net, meta, st]} />;
  if (!net.data || !meta.data) return <SplitSkeleton />;

  const sc = show.data?.data?.showcase;
  // while another scenario loads, the last one stays on screen and a quiet line says so:
  // blanking the readings made the panel collapse and grow back on every switch
  const agg = st.data?.header.aggregates;
  const updating = st.isPlaceholderData;
  const center: [number, number] = meta.data.center;
  const ser = series.data?.data;
  const nNonfirm = sc ? sc.nonfirm_cids.length : 0;
  const scnText = sc
    ? scenario === "static"
      ? t("scn.static.d", { n: sc.firm })
      : scenario === "uncontrolled"
        ? t("scn.uncontrolled.d", { n: sc.adopters, h: sc.verification.uncontrolled_violation_hours, year })
        : t("scn.doe.d", { n: sc.adopters, k: nNonfirm })
    : "";

  return (
    <div className="split">
      <div className="map-col" ref={colRef}>
        <div className="map-wrap">
          <MapView center={center} zoom={16} pitch={50} bearing={-18} setup={setup} onReady={onReady} ariaLabel={t("twin.title", { n: houses.length })}>
            <Daylight center={center} year={year} />
            {st.isLoading && <div className="map-loading" role="status">{t("ui.loading")}</div>}
            {updating && <div className="updating" role="status">{t("twin.updating")}</div>}
            <div className="map-toolbar"><button className="icon-btn" aria-label={t("twin.resetView")} title={t("twin.resetView")} onClick={() => { if (mapRef.current && net.data.bbox) { mapRef.current.jumpTo({ pitch: 50, bearing: -18 }); fitTo(mapRef.current, net.data.bbox, 60); } }}><Icon name="reset" size={17} /></button></div>
            <MapLegend colorBy={colorBy} theme={theme} />
            <TipLayer register={tipRef} housesById={housesById} cablesById={cablesById} rows={rows} st={st} />
          </MapView>
        </div>
        <Timeline
          agg={agg}
          hours={st.data?.header.hours ?? 8760}
          year={year}
          showcaseHour={meta.data.showcase_hour}
          pending={t(st.isError ? "ui.unavailable" : "ui.loading")}
        />
        <div className={`houses-region ${openTable ? "open" : ""}`}>
          <button className="region-toggle" aria-expanded={openTable} onClick={() => setOpenTable(!openTable)}>
            <Icon name={openTable ? "sort" : "next"} size={15} />
            <span>{t("dt.houses", { n: houses.length })}</span>
            <span className="caption-muted">{t("dt.hint")}</span>
          </button>
          {openTable && (
            <HouseTable houses={houses} rows={rows} st={st} curtailed={curtailed} selectedCid={selectedCid} select={select} year={year} />
          )}
        </div>
      </div>

      <aside className="panel" data-playing={playing}>
        <h1 className="panel-title">{t("twin.title", { n: houses.length })}</h1>
        {picked && <Inspector house={picked} rows={rows} st={st} onClear={() => select(null)} />}

        <HourReadings agg={agg} ser={ser} year={year} center={center} scenario={scenario} />

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

        <Conditions agg={agg} ser={ser} year={year} center={center} />
        <p className="source-line">{t("twin.sub", { addr: meta.data.substation?.address ?? "" })}</p>
      </aside>
    </div>
  );
}

/** Dusk and night over the map, from the sun's elevation at this hour. */
function Daylight({ center, year }: { center: [number, number]; year: number }) {
  const hour = useStore((s) => s.hour);
  const elev = solarElevation(yearStart(year) + (hour + 0.5) * 3600_000, center[1], center[0]);
  return <div className="daylight" style={{ opacity: nightOpacity(elev) }} />;
}

/** Hold, play and step through the year; the strip and the slider move the hour. */
function Timeline({ agg, hours, year, showcaseHour, pending }: {
  agg: any; hours: number; year: number; showcaseHour: unknown; pending: string;
}) {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const hour = useStore((s) => s.hour);
  const playing = useStore((s) => s.playing);
  const setHour = useStore((s) => s.setHour);
  const setPlaying = useStore((s) => s.setPlaying);
  const [speed, setSpeed] = useState(1);
  const captionId = useId();

  // playback: one hour per tick
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setHour((useStore.getState().hour + 1) % hours), 600 / speed);
    return () => window.clearInterval(id);
  }, [playing, hours, setHour, speed]);

  useEffect(() => () => setPlaying(false), [setPlaying]);

  return (
    <div className="yearstrip">
      <div className="yearstrip-controls">
        <span className="yearstrip-when">{hourLabel(lang, year, hour)}</span>
        <div className="timeline-buttons">
          <button className="icon-btn" title={t("twin.previous")} aria-label={t("twin.previous")} disabled={!agg || hour === 0} onClick={() => { setPlaying(false); setHour(Math.max(0, hour - 1)); }}><Icon name="previous" size={17} /></button>
          <button className="icon-btn play-button" disabled={!agg} onClick={() => setPlaying(!playing)} aria-pressed={playing} aria-label={playing ? t("twin.pause") : t("twin.play")}><Icon name={playing ? "pause" : "play"} size={16} />{playing ? t("twin.pause") : t("twin.play")}</button>
          <button className="icon-btn" title={t("twin.next")} aria-label={t("twin.next")} disabled={!agg || hour >= hours - 1} onClick={() => { setPlaying(false); setHour(Math.min(hours - 1, hour + 1)); }}><Icon name="next" size={17} /></button>
        </div>
        <div className="timeline-tools">
          <select className="theme-select" aria-label={t("twin.speed")} value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{SPEEDS}</select>
          <button className="icon-btn" disabled={typeof showcaseHour !== "number"} onClick={() => { setPlaying(false); setHour(showcaseHour as number); }}>{t("twin.peak")}</button>
        </div>
        <p className="caption-muted yearstrip-caption" id={captionId}>{t("twin.strip", { year })}</p>
      </div>
      {agg ? (
        <YearStrip values={agg.trafo_loading} hours={hours} year={year} label={t("twin.hour")} describedBy={captionId} />
      ) : (
        <div className="caption">{pending}</div>
      )}
    </div>
  );
}

const SPEEDS = [1, 4, 8].map((s) => <option key={s} value={s}>{s}×</option>);

/**
 * The grid at this hour first: whether it is within its limits, the transformer and the
 * busiest cable against their ratings, the voltage band. The weather and price that
 * explain it come after, in two columns. The hour itself is read off the strip.
 */
function HourReadings({ agg, scenario }: {
  agg: any; ser: any; year: number; center: [number, number]; scenario: string;
}) {
  const t = useT();
  const f = useFmt();
  const hour = useStore((s) => s.hour);
  const violation = agg ? agg.violation[hour] === 1 : false;
  const trafoRev = agg ? agg.trafo_p_kw[hour] < 0 : false;
  return (
    <div className="stack">
      <h2 className="hour-verdict">
        {agg ? (violation ? <Status kind="critical">{t("twin.atHour.over")}</Status> : <Status kind="good">{t("twin.atHour.within")}</Status>) : t("ui.loading")}
      </h2>
      {agg && (
        <>
          <Meter label={trafoRev ? t("twin.trafoRev") : t("twin.trafo")} value={agg.trafo_loading[hour]} max={140} limit={100} />
          <Meter label={t("twin.line")} value={agg.max_line_loading[hour]} max={140} limit={100} />
          <div className="tiles cols-2">
            <Tile label={<Term id="pu">{t("twin.vmax")}</Term>} value={f(agg.vmax[hour], 3)} unit="pu" />
            <Tile label={t("twin.vmin")} value={f(agg.vmin[hour], 3)} unit="pu" />
            {scenario === "doe" && agg.curtailed_kw && <Tile label={t("twin.curtailed")} value={f(agg.curtailed_kw[hour], 1)} unit="kW" />}
          </div>
        </>
      )}
    </div>
  );
}

/** The weather and price of the hour, and the solar and demand they make: what explains the verdict. */
function Conditions({ agg, ser, year, center }: { agg: any; ser: any; year: number; center: [number, number] }) {
  const t = useT();
  const f = useFmt();
  const hour = useStore((s) => s.hour);
  const elev = solarElevation(yearStart(year) + (hour + 0.5) * 3600_000, center[1], center[0]);
  return (
    <div className="stack">
      <h3>{t("twin.conditions")}</h3>
      <div className="tiles cols-2">
        <Tile label={t("twin.sun")} value={f(elev, 0)} unit="°" />
        <Tile mark="real" label={t("twin.ghi")} value={ser ? f(ser.ghi[hour], 0) : "–"} unit="W/m²" />
        <Tile mark="real" label={t("twin.temp")} value={ser ? f(ser.t_air[hour], 1) : "–"} unit="°C" />
        <Tile mark="real" label={t("twin.price")} value={ser?.price_sek_kwh ? f(ser.price_sek_kwh[hour], 2) : "–"} unit="kr/kWh" />
        <Tile mark="synthetic" label={t("twin.pv")} value={agg ? f(agg.pv_kw[hour], 0) : "–"} unit="kW" />
        <Tile mark="synthetic" label={t("twin.load")} value={agg ? f(agg.load_kw[hour], 0) : "–"} unit="kW" />
      </div>
      <p className="source-line">{t("twin.sources")}</p>
    </div>
  );
}

/** A house's connection, keyed as the plan draws it. */
function HouseStatus({ status }: { status: string }) {
  const t = useT();
  return (
    <span className={`house-status ${status}`}>
      {status !== "none" && <i className={`house-key ${status}`} aria-hidden />}
      {t(`twin.status.${status}`)}
    </span>
  );
}

/**
 * The selected house: its state at this hour, where it sits on the network, and the next
 * step for it, a connection request.
 */
function Inspector({ house, rows, st, onClear }: { house: any; rows: Rows | null; st: GridState; onClear: () => void }) {
  const t = useT();
  const f = useFmt();
  const hour = useStore((s) => s.hour);
  const r = rows?.busRow.get(house.bus);
  const v = st.data && r !== undefined ? st.data.voltage(r, hour) : null;
  const p = st.data ? st.data.power(house.cid, hour) : null;
  return (
    <div className="stack inspector">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="inspector-title">{house.address || t("dt.unnamed", { id: house.cid })}</h2>
        <button className="icon-btn" aria-label={t("twin.clear")} title={t("twin.clear")} onClick={onClear}>
          <Icon name="close" size={15} />
        </button>
      </div>
      <div className="tiles cols-2">
        <Tile label={t("dt.voltage")} value={v == null ? "–" : f(v, 3)} unit="pu" />
        <Tile label={t("dt.net")} value={p == null ? "–" : f(p, 1)} unit="kW" />
        <Tile label={t("dt.status")} value={<span className="t-word"><HouseStatus status={house.pv_status} /></span>} />
        <Tile label={t("dt.kwp")} value={f(house.kwp, 1)} unit="kWp" />
        <Tile label={t("dt.feeder")} value={house.feeder + 1} />
        <Tile label={t("dt.dist")} value={f(house.dist_m, 0)} unit="m" />
      </div>
      <div>
        <button className="btn" onClick={() => useStore.getState().setScreen("request")}>{t("net.test")}</button>
      </div>
    </div>
  );
}

/** The 120 houses with this hour's voltage and net power; mounted only while open. */
function HouseTable({ houses, rows, st, curtailed, selectedCid, select, year }: {
  houses: any[]; rows: Rows | null; st: GridState; curtailed: Map<number, number>;
  selectedCid: number | null; select: (cid: number | null) => void; year: number;
}) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const hour = useStore((s) => s.hour);

  // what does not change with the hour is built once (rerender-split-combined-hooks)
  const base = useMemo(
    () => houses.map((h: any) => {
      const p = h.properties;
      return {
        cid: p.cid as number,
        bus: p.bus as number,
        address: (p.address as string) || t("dt.unnamed", { id: p.cid }),
        feeder: p.feeder as number,
        status: p.pv_status as "firm" | "nonfirm" | "none",
        kwp: p.kwp as number,
        fuse: p.fuse_a as number,
        dist: p.dist_m as number,
        curtailed: curtailed.get(p.cid) ?? null,
      };
    }),
    [houses, curtailed, t],
  );

  const tableRows: HouseRow[] = useMemo(
    () => base.map(({ bus, ...b }) => {
      const r = rows?.busRow.get(bus);
      return {
        ...b,
        v: st.data && r !== undefined ? st.data.voltage(r, hour) : null,
        p: st.data ? st.data.power(b.cid, hour) : null,
      };
    }),
    [base, rows, st.data, hour],
  );

  const cols: Col<HouseRow>[] = useMemo(
    () => [
      { key: "address", label: t("dt.address"), value: (r) => r.address },
      { key: "feeder", label: t("dt.feeder"), value: (r) => r.feeder + 1, numeric: true },
      {
        key: "status",
        label: t("dt.status"),
        value: (r) => t(`twin.status.${r.status}`),
        // the house as the plan draws it: a connection type is not a pass or a fail, so it
        // gets the plan's key rather than a status icon, and a house without solar none
        cell: (r) => <HouseStatus status={r.status} />,
      },
      { key: "kwp", label: t("dt.kwp"), unit: "kWp", value: (r) => r.kwp, cell: (r) => f(r.kwp, 1), numeric: true },
      { key: "fuse", label: t("dt.fuse"), unit: "A", value: (r) => r.fuse, numeric: true },
      { key: "dist", label: t("dt.dist"), unit: "m", value: (r) => r.dist, cell: (r) => f(r.dist, 0), numeric: true },
      {
        key: "v",
        label: t("dt.voltage"),
        unit: "pu",
        value: (r) => r.v,
        cell: (r) => (r.v == null ? "–" : <span className="swatch-value"><i style={{ background: voltageColor(tokens(), r.v, V_SPAN) }} />{f(r.v, 3)}</span>),
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

  const onSelect = useCallback((r: HouseRow) => select(r.cid), [select]);

  return (
    <DataTable
      rows={tableRows}
      cols={cols}
      rowKey={rowKey}
      caption={t("dt.caption", { n: houses.length, when: hourLabel(lang, year, hour) })}
      searchLabel={t("dt.searchHouses")}
      selected={selectedCid}
      onSelect={onSelect}
      initialSort="address"
    />
  );
}

/** The map's key, drawn from the colour functions the map uses so the two always agree. */
const MapLegend = memo(function MapLegend({ colorBy }: { colorBy: ColorBy; theme: string }) {
  const t = useT();
  const f = useFmt();
  // the voltage limits are model inputs, not constants of this component
  const ass = useApi<any>("/assumptions");
  const limit = (key: string): number | null =>
    (ass.data?.assumptions as any[] | undefined)?.find((a) => a.key === key)?.value ?? null;
  const vLo = limit("v_min_pu");
  const vHi = limit("v_max_pu");
  const tk = tokens();
  const seqGrad = rampCss((x) => loadingColor(tk, x * 100));
  // power is drawn on the voltage poles (powerColor), so both scales share one bar
  const divGrad = rampCss((x) => voltageColor(tk, 1 + (2 * x - 1) * V_SPAN, V_SPAN));
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
        <span className="legend-key">
          <i style={{ background: tk.loadOver }} />
          <span>{t("twin.legend.over")}</span>
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
          <span className="caption-muted">
            {t("twin.legend.vnote", { p: f(V_SPAN * 100, 0) })}
            {vLo != null && vHi != null ? ` ${t("twin.legend.limits", { lo: f(vLo, 2), hi: f(vHi, 2) })}` : ""}
          </span>
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
});

/**
 * Holds the hover state, so a mouse move re-renders the tooltip and nothing else
 * (rerender-use-ref-transient-values): the map writes through `register`.
 */
function TipLayer({ register, housesById, cablesById, rows, st }: {
  register: MutableRefObject<(tip: Tip | null) => void>;
  housesById: Map<number, any>; cablesById: Map<number, any>; rows: Rows | null; st: GridState;
}) {
  const [tip, setTip] = useState<Tip | null>(null);
  useEffect(() => {
    register.current = setTip;
    return () => { register.current = () => {}; };
  }, [register]);
  if (!tip || !rows || !st.data) return null;
  return <Tooltip tip={tip} housesById={housesById} cablesById={cablesById} rows={rows} st={st} />;
}

function Tooltip({ tip, housesById, cablesById, rows, st }: {
  tip: Tip; housesById: Map<number, any>; cablesById: Map<number, any>; rows: Rows; st: GridState;
}) {
  const t = useT();
  const f = useFmt();
  const hour = useStore((s) => s.hour);
  if (!st.data) return null;
  const style = { transform: `translate(${tip.x + 14}px, ${tip.y + 14}px)` };
  if (tip.kind === "house") {
    const h = housesById.get(tip.id);
    if (!h) return null;
    const r = rows.busRow.get(h.bus);
    const v = r !== undefined ? st.data.voltage(r, hour) : NaN;
    const p = st.data.power(h.cid, hour);
    return (
      <div className="map-tooltip" style={style}>
        <div className="tt-title">{h.address || t("dt.unnamed", { id: h.cid })}</div>
        <div className="tt-row"><span>{t("dt.net")}</span><b>{f(Math.abs(p), 1)} kW {p >= 0 ? t("twin.tt.export") : t("twin.tt.import")}</b></div>
        <div className="tt-row"><span>{t("dt.voltage")}</span><b>{f(v, 3)} pu</b></div>
        <div className="tt-row"><span>{t("dt.status")}</span><b>{t(`twin.status.${h.pv_status}`)}</b></div>
        <div className="tt-row"><span>{t("dt.kwp")}</span><b>{f(h.kwp, 1)} kWp</b></div>
        <div className="tt-row"><span>{t("dt.fuse")}</span><b>{h.fuse_a} A</b></div>
      </div>
    );
  }
  const c = cablesById.get(tip.id);
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
