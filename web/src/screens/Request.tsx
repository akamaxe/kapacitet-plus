import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { MapView, fitTo } from "../components/MapView";
import { Chart, axisStyle } from "../components/Chart";
import { ChartOrTable, Legend, Loading, Status, Tile } from "../components/bits";
import { API, useApi } from "../lib/api";
import { tokens, type Tokens } from "../lib/colors";
import { hourLabel, monthNames, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

type Stage = "static" | "envelope" | "verify" | "risk" | "economics" | "done";
const STAGES: Stage[] = ["static", "envelope", "verify", "risk", "economics"];

interface Job {
  id: string;
  stages: Partial<Record<Stage, number>>;
  result: any | null;
  error: string | null;
}

export default function Request() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const theme = useStore((s) => s.theme);
  const cid = useStore((s) => s.selectedCid);
  const select = useStore((s) => s.select);
  const meta = useApi<any>("/meta");
  const net = useApi<any>("/network");
  const show = useApi<any>("/showcase");
  const [baseline, setBaseline] = useState<"static" | "doe">("static");
  const [pv, setPv] = useState(10);
  const [ev, setEv] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const mapRef = useRef<MLMap | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const houses = useMemo(() => net.data?.features.filter((x: any) => x.properties.layer === "house") ?? [], [net.data]);
  const S = show.data?.data?.showcase;
  const status = useMemo(() => {
    const m = new Map<number, string>();
    if (!S) return m;
    const firm = new Set<number>(S.firm_cids);
    const nonfirm = new Set<number>(S.nonfirm_cids);
    houses.forEach((h: any) => {
      const c = h.properties.cid;
      m.set(c, firm.has(c) ? "firm" : baseline === "doe" && nonfirm.has(c) ? "nonfirm" : "none");
    });
    return m;
  }, [S, houses, baseline]);

  const house = houses.find((h: any) => h.properties.cid === cid)?.properties;

  useEffect(() => {
    if (house) setPv(Math.max(3, Math.round(house.kwp)));
  }, [house?.cid]);

  // ------------------------------------------------------------------ map
  // setup/onReady run from map events, so they call the latest painter through this ref
  const paintRef = useRef<(map: MLMap | null) => void>(() => {});
  const setup = useCallback((map: MLMap) => {
    if (!net.data) return;
    const tk = tokens();
    const fc = (features: any[]) => ({ type: "FeatureCollection", features });
    const ctx = net.data.features.filter((x: any) => x.properties.layer === "context");
    const cables = net.data.features.filter((x: any) => x.properties.layer === "cable" && x.properties.kind === "main");
    const hs = houses.map((h: any) => ({ ...h, properties: { ...h.properties, height: Math.max(1, h.properties.levels) * 3.2 } }));
    if (!map.getSource("houses")) {
      map.addSource("context", { type: "geojson", data: fc(ctx) } as any);
      map.addSource("mains", { type: "geojson", data: fc(cables) } as any);
      map.addSource("houses", { type: "geojson", data: fc(hs), promoteId: "cid" } as any);
    }
    if (!map.getLayer("houses")) {
      map.addLayer({ id: "context", type: "fill", source: "context", paint: { "fill-color": tk.border, "fill-opacity": 0.7 } });
      map.addLayer({ id: "mains", type: "line", source: "mains", paint: { "line-color": tk.borderStrong, "line-width": 1.5 } });
      map.addLayer({
        id: "houses", type: "fill-extrusion", source: "houses",
        paint: {
          "fill-extrusion-color": ["coalesce", ["feature-state", "color"], tk.borderStrong],
          "fill-extrusion-height": ["case", ["boolean", ["feature-state", "sel"], false], ["*", ["get", "height"], 1.8], ["get", "height"]],
          "fill-extrusion-opacity": 0.95,
        },
      });
    }
    paintRef.current(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net.data, theme]);

  const paintHouses = useCallback((map: MLMap | null) => {
    if (!map || !map.getSource("houses")) return;
    const tk = tokens();
    houses.forEach((h: any) => {
      const c = h.properties.cid;
      const s = status.get(c);
      const color = c === cid ? tk.cat[0] : s === "firm" ? tk.text2 : s === "nonfirm" ? tk.accent : tk.borderStrong;
      map.setFeatureState({ source: "houses", id: c }, { color, sel: c === cid });
    });
  }, [houses, status, cid]);

  paintRef.current = paintHouses;
  useEffect(() => paintHouses(mapRef.current), [paintHouses]);

  const onReady = useCallback((map: MLMap) => {
    mapRef.current = map;
    if (net.data?.bbox) fitTo(map, net.data.bbox, 50);
    map.on("click", "houses", (e: MapMouseEvent & { features?: any[] }) => {
      const c = e.features?.[0]?.properties?.cid;
      if (c !== undefined) {
        select(Number(c));
        setJob(null);
      }
    });
    map.on("mouseenter", "houses", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "houses", () => (map.getCanvas().style.cursor = ""));
    paintRef.current(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net.data]);

  // ------------------------------------------------------------------ evaluation
  const evaluate = async () => {
    if (cid === null) return;
    esRef.current?.close();
    setJob({ id: "", stages: {}, result: null, error: null });
    const r = await fetch(`${API}/requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cid, pv_kwp: pv, ev_kw: ev, baseline }),
    });
    if (!r.ok) {
      setJob({ id: "", stages: {}, result: null, error: `${r.status}` });
      return;
    }
    const { id } = await r.json();
    const es = new EventSource(`${API}/requests/${id}/events`);
    esRef.current = es;
    const mark = (stage: Stage) => (e: MessageEvent) => {
      const p = JSON.parse(e.data);
      setJob((j) => (j ? { ...j, id, stages: { ...j.stages, [stage]: p.ms ?? 0 } } : j));
    };
    STAGES.forEach((s) => es.addEventListener(s, mark(s) as any));
    es.addEventListener("done", async () => {
      es.close();
      const res = await fetch(`${API}/requests/${id}`).then((x) => x.json());
      setJob((j) => (j ? { ...j, stages: { ...j.stages, done: 0 }, result: res } : j));
    });
    es.addEventListener("error", () => {
      es.close();
    });
  };

  useEffect(() => () => esRef.current?.close(), []);

  if (!net.data || !meta.data) return <Loading />;
  const year = meta.data.reference_year;
  const res = job?.result;
  const running = !!job && !job.result && !job.error;

  return (
    <div className="split wide-panel">
      <div className="map-col">
        <div className="map-wrap">
          <MapView center={meta.data.center} zoom={16} pitch={45} bearing={-18} setup={setup} onReady={onReady} ariaLabel={t("req.title")}>
            <div className="map-legend" style={{ width: 220 }}>
              <Legend items={[
                { color: tokens().text2, label: `${t("twin.status.firm")}` },
                ...(baseline === "doe" ? [{ color: tokens().accent, label: t("twin.status.nonfirm") }] : []),
                { color: tokens().borderStrong, label: t("twin.status.none") },
                { color: tokens().cat[0], label: t("req.house") },
              ]} />
            </div>
          </MapView>
        </div>
      </div>

      <aside className="panel">
        <div className="stack">
          <h2>{t("req.title")}</h2>
          <div className="stack" style={{ gap: 6 }}>
            <span className="caption">{t("req.baseline")}</span>
            <div className="seg">
              <button aria-pressed={baseline === "static"} onClick={() => { setBaseline("static"); setJob(null); }}>{t("req.base.static")}</button>
              <button aria-pressed={baseline === "doe"} onClick={() => { setBaseline("doe"); setJob(null); }}>{t("req.base.doe")}</button>
            </div>
          </div>
        </div>

        {!house ? (
          <p className="caption">{t("req.pick")}</p>
        ) : (
          <>
            <div className="stack">
              <h3>{house.address || house.osm_id}</h3>
              <dl className="kv">
                <dt>{t("req.roof")}</dt><dd>{f(house.kwp, 1)} kWp</dd>
                <dt>{t("req.orientation")}</dt><dd>{house.east_west ? "E–W" : `${f(house.azimuth)}°`}, {f(house.tilt)}°</dd>
                <dt>{t("req.fuse")}</dt><dd>{house.fuse_a} A ({f(house.fuse_kva, 1)} kVA)</dd>
                <dt>{t("req.feeder")}</dt><dd>{house.feeder + 1}</dd>
                <dt>{t("req.distance")}</dt><dd>{f(house.dist_m)} m</dd>
              </dl>
              {status.get(house.cid) !== "none" && <p className="caption-muted">{t("req.hadpv")}</p>}
            </div>

            <div className="stack">
              <div className="field">
                <label htmlFor="pv">{t("req.pv")}</label>
                <output htmlFor="pv">{f(pv)} kWp</output>
                <input id="pv" type="range" min={0} max={20} step={1} value={pv} onChange={(e) => setPv(Number(e.target.value))} />
              </div>
              <div className="stack" style={{ gap: 6 }}>
                <span className="caption">{t("req.ev")}</span>
                <div className="seg">
                  {[0, 3.7, 11].map((v) => (
                    <button key={v} aria-pressed={ev === v} onClick={() => setEv(v)}>{v === 0 ? t("req.none") : `${f(v, v % 1 ? 1 : 0)} kW`}</button>
                  ))}
                </div>
              </div>
              <div>
                <button className="btn" disabled={running || (pv === 0 && ev === 0)} onClick={evaluate}>
                  {running ? t("req.running") : t("req.evaluate")}
                </button>
              </div>
            </div>
          </>
        )}

        {job && (
          <div className="steps" aria-live="polite">
            {STAGES.map((s, i) => {
              const done = job.stages[s] !== undefined;
              const active = !done && STAGES.slice(0, i).every((p) => job.stages[p] !== undefined) && running;
              return (
                <div key={s} className={`step ${done ? "done" : active ? "active" : ""}`}>
                  <span className="dot" />
                  <span>{t(`req.stage.${s}`)}</span>
                  <span className="ms">{done ? `${f(job.stages[s]!, 0)} ms` : ""}</span>
                </div>
              );
            })}
            {res && <p className="caption-muted">{t("req.seconds", { s: f(res.seconds, 2) })}</p>}
          </div>
        )}

        {res && <Result res={res} year={year} lang={lang} />}
      </aside>
    </div>
  );
}

/** Static-study reason in the current language (falls back to the API's English text). */
function reasonText(t: (k: string, v?: any) => string, f: (x: any, d?: number) => string, s: any): string {
  if (!s?.reason_code) return s?.reason ?? "";
  const pu = s.reason_code === "over_v" || s.reason_code === "under_v";
  return t(`req.reason.${s.reason_code}`, { v: s.reason_value == null ? "" : f(s.reason_value, pu ? 3 : 0) });
}

function Result({ res, year, lang }: { res: any; year: number; lang: string }) {
  const t = useT();
  const f = useFmt();
  const sum = res.summary;
  const pvReq = res.request.pv_kwp > 0;
  const evReq = res.request.ev_kw > 0;
  const refYear = res.per_year.find((e: any) => e.year === year);
  const prof = res.profiles;
  // does the envelope ever go below the system's own export capacity in the reference year?
  const everLimited = !!prof?.envelope_min_month_hour &&
    (prof.envelope_min_month_hour as number[][]).some((row) => row.some((v) => v < prof.cap_kw - 0.05));

  return (
    <div className="stack" style={{ gap: 20 }}>
      {pvReq && (
        <div className="verdicts">
          <div className="verdict">
            <span className="v-method">{t("req.static")}</span>
            {sum.pv_static_approved ? <Status kind="good">{t("req.approved")}</Status> : <Status kind="critical">{t("req.rejected")}</Status>}
            <span className="v-detail">{reasonText(t, f, res.static.pv)}</span>
          </div>
          <div className="verdict">
            <span className="v-method">{t("req.doe")}</span>
            {sum.pv_curtailment_ref_pct > 0.005 ? <Status kind="warning">{t("req.approvedLimits")}</Status> : <Status kind="good">{t("req.approved")}</Status>}
            <span className="v-big">{f(sum.pv_curtailment_ref_pct, 1)} %</span>
            <span className="v-detail">
              {refYear?.hours_limited ? `${t("req.curtailed", { z: f(sum.pv_curtailment_ref_pct, 1), year })}, ${t("req.hours", { h: refYear.hours_limited })}` : t("req.noLimit", { year })}
            </span>
            {res.verification?.hours_checked !== undefined && (
              <span className="caption-muted">
                {res.verification.hours_checked > 0
                  ? t("req.verified", { n: res.verification.hours_checked, v: res.verification.violations_after })
                  : t("req.verifiedNone")}
              </span>
            )}
          </div>
        </div>
      )}

      {evReq && (
        <div className="tiles">
          <Tile label={t("req.ev.static")} value={res.static.ev.approved ? <Status kind="good">{t("req.approved")}</Status> : <Status kind="critical">{t("req.rejected")}</Status>} note={reasonText(t, f, res.static.ev)} />
          <Tile label={t("req.ev.unmet")} value={f(sum.ev_unmet_pct, 2)} unit="%" note={t("req.weatherYears", { n: res.per_year.length })} />
          <Tile label={t("req.ev.limited")} value={f(sum.ev_hours_limited_ref)} unit="h" note={`${year}`} />
          <Tile label={t("req.ev.energy")} value={f(res.per_year.reduce((s: number, e: any) => s + (e.ev_requested_kwh ?? 0), 0) / Math.max(1, res.per_year.length))} unit="kWh" note={t("req.ev.energyNote")} />
        </div>
      )}

      {pvReq && prof?.envelope_min_month_hour && (
        <section className="stack">
          <h3>{t("req.envMap")}</h3>
          {everLimited ? (
            <>
              <p className="caption-muted">{t("req.envMap.caption", { year })}</p>
              <Chart ariaLabel={t("req.envMap")} height={230} deps={[res, lang]} build={(tk) => envOption(tk, prof, lang, t)} />
            </>
          ) : (
            <Status kind="good">{t("req.fullCap", { year })}</Status>
          )}
        </section>
      )}

      {pvReq && prof?.envelope_kw && <WeekChart prof={prof} year={year} lang={lang} />}

      {pvReq && res.risk?.histogram && (
        <section className="stack">
          <h3>{t("req.risk")}</h3>
          <p className="caption-muted">{t("req.risk.caption")}</p>
          <div className="tiles" style={{ gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}>
            <Tile label={t("cmp.risk.mean")} value={f(res.risk.mean_pct, 2)} unit="%" />
            <Tile label={t("cmp.risk.p90")} value={f(res.risk.p90_pct, 2)} unit="%" />
            <Tile label={t("cmp.risk.cvar")} value={f(res.risk.cvar95_pct, 2)} unit="%" />
          </div>
          <Chart ariaLabel={t("req.risk")} height={170} deps={[res, lang]} build={(tk) => histOption(tk, res.risk.histogram, t)} />
        </section>
      )}

      {pvReq && res.economics?.by_year && (
        <section className="stack">
          <h3>{t("req.money")}</h3>
          <div className="tiles" style={{ gridTemplateColumns: "repeat(3, minmax(0,1fr))" }}>
            <Tile label={t("req.money.curtailed")} value={f(res.economics.curtailed_sek_mean)} unit="kr/yr" />
            <Tile label={t("req.money.export")} value={f(res.economics.export_sek_mean)} unit="kr/yr" />
            <Tile label={t("req.money.self")} value={f(res.economics.self_use_spot_sek_mean)} unit="kr/yr" />
          </div>
          <p className="caption-muted">{t("req.money.note")}</p>
        </section>
      )}
    </div>
  );
}

function WeekChart({ prof, year, lang }: { prof: any; year: number; lang: string }) {
  const t = useT();
  // the week (Mon–Sun blocks from 1 Jan) with the most curtailment; fall back to midsummer
  const curt: number[] = prof.curtailed_kw;
  let best = 25;
  let bestSum = -1;
  for (let w = 0; w < 52; w++) {
    let s = 0;
    for (let h = w * 168; h < (w + 1) * 168; h++) s += curt[h] ?? 0;
    if (s > bestSum) {
      bestSum = s;
      best = w;
    }
  }
  const anyCurtailed = bestSum > 0;
  if (!anyCurtailed) best = 25;
  const a = best * 168;
  const hours = Array.from({ length: 168 }, (_, i) => a + i);
  return (
    <section className="stack">
      <h3>{anyCurtailed ? t("req.week") : t("req.weekNone")}</h3>
      <p className="caption-muted">{hourLabel(lang, year, a)}</p>
      <Legend items={[
        { color: "var(--accent)", label: t("req.week.pv") },
        { color: "var(--cat-1)", label: t("req.week.limit"), line: true },
        { color: "var(--cat-3)", label: t("req.week.curtailed") },
      ]} />
      <ChartOrTable
        chart={<Chart ariaLabel={t("req.week")} height={220} deps={[prof, lang]} build={(tk) => weekOption(tk, prof, hours, year, lang, t)} />}
        table={
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            <table className="data">
              <thead><tr><th>{t("tbl.hour")}</th><th className="r">{t("req.week.pv")}, kW</th><th className="r">{t("req.week.limit")}, kW</th><th className="r">{t("req.week.curtailed")}, kW</th></tr></thead>
              <tbody>
                {hours.filter((h) => prof.pv_kw[h] > 0).map((h) => (
                  <tr key={h}><td>{hourLabel(lang, year, h)}</td><td className="r">{prof.pv_kw[h].toFixed(1)}</td><td className="r">{prof.envelope_kw[h].toFixed(1)}</td><td className="r">{prof.curtailed_kw[h].toFixed(1)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        }
      />
    </section>
  );
}

function envOption(tk: Tokens, prof: any, lang: string, t: (k: string) => string) {
  const grid: number[][] = prof.envelope_min_month_hour;
  const cap = prof.cap_kw;
  const data: [number, number, number][] = [];
  for (let h = 0; h < 24; h++) for (let m = 0; m < 12; m++) {
    const v = grid[h][m];
    if (v < cap - 0.05) data.push([m, h, Number(v.toFixed(2))]);
  }
  return {
    grid: { left: 44, right: 70, top: 6, bottom: 26 },
    xAxis: { type: "category", data: monthNames(lang), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "category", data: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}`), inverse: true, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 3 } },
    visualMap: {
      min: 0, max: cap, orient: "vertical", right: 0, top: "middle", itemHeight: 120, itemWidth: 10,
      textStyle: { color: tk.muted, fontFamily: tk.font }, text: [`${cap.toFixed(1)} kW`, "0"],
      inRange: { color: [tk.load[6], tk.load[4], tk.load[2], tk.load[0]] },
    },
    tooltip: { formatter: (p: any) => `${monthNames(lang)[p.value[0]]} ${String(p.value[1]).padStart(2, "0")}:00<br/>${t("req.tt.lowest")} <b>${p.value[2].toFixed(1)} kW</b>` },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: tk.surface, borderWidth: 2, borderRadius: 2 } }],
  };
}

function weekOption(tk: Tokens, prof: any, hours: number[], year: number, lang: string, t: (k: string) => string) {
  const x = hours.map((h) => hourLabel(lang, year, h));
  return {
    grid: { left: 40, right: 12, top: 12, bottom: 30 },
    xAxis: { type: "category", data: x, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 23, formatter: (v: string) => v.split(",")[0].replace(/\s\d{4}$/, "") } },
    yAxis: { type: "value", ...axisStyle(tk), name: "kW" },
    tooltip: { trigger: "axis" },
    series: [
      { type: "line", name: t("req.week.pv"), data: hours.map((h) => prof.pv_kw[h]), symbol: "none", lineStyle: { width: 2, color: tk.accent }, areaStyle: { color: tk.accent, opacity: 0.1 } },
      { type: "line", name: t("req.week.limit"), data: hours.map((h) => prof.envelope_kw[h]), symbol: "none", step: "middle", lineStyle: { width: 2, color: tk.cat[0] } },
      { type: "bar", name: t("req.week.curtailed"), data: hours.map((h) => prof.curtailed_kw[h]), itemStyle: { color: tk.cat[2], borderRadius: [2, 2, 0, 0] }, barWidth: 3 },
    ],
  };
}

function histOption(tk: Tokens, hist: { counts: number[]; edges: number[] }, t: (k: string) => string) {
  const mids = hist.counts.map((_, i) => (hist.edges[i] + hist.edges[i + 1]) / 2);
  return {
    grid: { left: 40, right: 12, top: 10, bottom: 28 },
    xAxis: { type: "category", data: mids.map((m) => m.toFixed(2)), ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 5, formatter: (v: string) => `${Number(v).toFixed(1)} %` } },
    yAxis: { type: "value", ...axisStyle(tk), axisLabel: { show: false } },
    tooltip: { trigger: "item", formatter: (p: any) => `${Number(p.name).toFixed(2)} %<br/><b>${p.value}</b> ${t("req.tt.years")}` },
    series: [{ type: "bar", data: hist.counts, itemStyle: { color: tk.accent, borderRadius: [4, 4, 0, 0] }, barCategoryGap: "12%" }],
  };
}
