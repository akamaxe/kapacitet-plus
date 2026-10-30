import { memo, useMemo, useState } from "react";
import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";

/**
 * The neighbourhood grid as a line network: every feeder leaves the substation like a
 * metro line and every house is a station on it.
 *
 * The drawing is schematic. A feeder takes the 45° direction nearest its real bearing
 * from the substation, its houses sit in order of their distance along the cable at an
 * even spacing, and its branches fold into one line. Two rules keep lines from crossing:
 * every bend turns the same way (counter-clockwise), and a second feeder in a direction
 * runs beside the first on that side and bends early.
 */

export type PvStatus = "firm" | "nonfirm" | "none";

export interface Station {
  cid: number;
  bus: number;
  address: string;
  status: PvStatus;
  dist: number;
  x: number;
  y: number;
}

export interface Line {
  /** zero-based, as the data has it; shown to people as feeder + 1 */
  feeder: number;
  points: [number, number][];
  stations: Station[];
  badge: { x: number; y: number };
}

export interface Layout {
  lines: Line[];
  byCid: Map<number, { line: Line; index: number }>;
  box: [number, number, number, number];
  mva: number | null;
}

/**
 * Each line spreads its stations over about the same length, so the drawing stays round
 * however many houses a feeder has; the spacing stays within these bounds, in drawing units.
 */
const ARM = 300;
const SP_MIN = 15;
const SP_MAX = 34;
/** the gap between two lines sharing a direction */
const OFF = 14;
/** a line with more stations than this bends once */
const BEND_AT = 9;
const HUB_R = 18;
const r1 = (v: number) => Math.round(v * 10) / 10;

const DIRS: [number, number][] = Array.from({ length: 8 }, (_, k) => {
  const a = (k * Math.PI) / 4;
  // y grows downwards on screen, so north is negative y
  return [Math.cos(a), -Math.sin(a)];
});

function layout(net: any): Layout | null {
  const feats: any[] = net?.features ?? [];
  const sub = feats.find((f) => f.properties?.layer === "substation");
  if (!sub) return null;
  const [slon, slat] = sub.geometry.coordinates as [number, number];
  const kx = Math.cos((slat * Math.PI) / 180);
  const groups = new Map<number, Omit<Station, "x" | "y">[]>();
  const centre = new Map<number, [number, number, number]>();
  for (const f of feats) {
    const p = f.properties ?? {};
    if (p.layer !== "house") continue;
    const list = groups.get(p.feeder) ?? [];
    list.push({
      cid: p.cid,
      bus: p.bus,
      address: p.address || "",
      status: p.pv_status === "firm" ? "firm" : p.pv_status === "nonfirm" ? "nonfirm" : "none",
      dist: p.dist_m ?? 0,
    });
    groups.set(p.feeder, list);
    const c = centre.get(p.feeder) ?? [0, 0, 0];
    centre.set(p.feeder, [c[0] + (p.lon - slon) * kx, c[1] + (p.lat - slat), c[2] + 1]);
  }
  // each feeder's bearing, counter-clockwise from east, taken from the mean of its houses
  const feeders = [...groups.keys()].map((feeder) => {
    const [x, y, n] = centre.get(feeder)!;
    const bearing = (Math.atan2(y / n, x / n) + 2 * Math.PI) % (2 * Math.PI);
    return { feeder, bearing, houses: groups.get(feeder)!.sort((a, b) => a.dist - b.dist) };
  });
  // the longest feeders choose first, so they get the direction nearest their bearing
  const slots = new Map<number, number>();
  const placed: { feeder: number; k: number; track: number; houses: Omit<Station, "x" | "y">[] }[] = [];
  for (const fd of [...feeders].sort((a, b) => b.houses.length - a.houses.length)) {
    const ideal = fd.bearing / (Math.PI / 4);
    const order = Array.from({ length: 8 }, (_, i) => i)
      .map((k) => ({ k, d: Math.min(Math.abs(ideal - k), 8 - Math.abs(ideal - k)) }))
      .sort((a, b) => a.d - b.d);
    let chosen: { k: number; track: number } | null = null;
    for (const track of [0, 1, 2]) {
      const hit = order.find(({ k, d }) => (slots.get(k) ?? 0) === track && d < 1.6 + track);
      if (hit) {
        chosen = { k: hit.k, track };
        break;
      }
    }
    if (!chosen) chosen = { k: order[0].k, track: slots.get(order[0].k) ?? 0 };
    slots.set(chosen.k, chosen.track + 1);
    placed.push({ feeder: fd.feeder, ...chosen, houses: fd.houses });
  }

  const lines: Line[] = [];
  const byCid = new Map<number, { line: Line; index: number }>();
  let minX = -HUB_R, maxX = HUB_R, minY = -HUB_R, maxY = HUB_R;
  for (const pl of placed) {
    const d = DIRS[pl.k];
    const d2 = DIRS[(pl.k + 1) % 8];
    // the counter-clockwise side of the direction, where a second track runs
    const side: [number, number] = [d[1], -d[0]];
    const o = pl.track * OFF;
    const start: [number, number] = [side[0] * o, side[1] * o];
    const n = pl.houses.length;
    const SP = Math.max(SP_MIN, Math.min(SP_MAX, ARM / n));
    const bendAfter = pl.track > 0 ? 3 : n > BEND_AT ? Math.round(n * 0.4) : n;
    // the first station sits well clear of the transformer, so the lines leave it legibly
    const first = Math.max(SP, 58);
    const pts: [number, number][] = [[0, 0], start];
    const stations: Station[] = [];
    let cur: [number, number] = start;
    // walk the line: the stub and the stations before the bend along d, the rest along d2
    const along = (dir: [number, number], len: number): [number, number] => [cur[0] + dir[0] * len, cur[1] + dir[1] * len];
    const bendLen = first + (Math.min(bendAfter, n) - 1) * SP + SP / 2;
    const corner = along(d, bendLen);
    const bends = bendAfter < n;
    for (let i = 0; i < n; i++) {
      let x: number, y: number;
      if (!bends || i < bendAfter) {
        x = start[0] + d[0] * (first + i * SP);
        y = start[1] + d[1] * (first + i * SP);
      } else {
        const t = SP / 2 + (i - bendAfter) * SP;
        x = corner[0] + d2[0] * t;
        y = corner[1] + d2[1] * t;
      }
      stations.push({ ...pl.houses[i], x: r1(x), y: r1(y) });
    }
    let end: [number, number];
    if (bends) {
      pts.push([r1(corner[0]), r1(corner[1])]);
      cur = corner;
      end = along(d2, SP / 2 + (n - bendAfter - 1) * SP + SP * 0.6);
    } else {
      end = along(d, first + (n - 1) * SP + SP * 0.6);
    }
    pts.push([r1(end[0]), r1(end[1])]);
    const last = bends ? d2 : d;
    const badge = { x: r1(end[0] + last[0] * 16), y: r1(end[1] + last[1] * 16) };
    const line: Line = { feeder: pl.feeder, points: pts, stations, badge };
    lines.push(line);
    stations.forEach((s, index) => byCid.set(s.cid, { line, index }));
    for (const [x, y] of [...pts, [badge.x, badge.y] as [number, number]]) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
  }
  const pad = 22;
  lines.sort((a, b) => a.feeder - b.feeder);
  return {
    lines,
    byCid,
    box: [r1(minX - pad), r1(minY - pad), r1(maxX - minX + 2 * pad), r1(maxY - minY + 2 * pad)],
    mva: typeof sub.properties?.trafo_sn_mva === "number" ? sub.properties.trafo_sn_mva : null,
  };
}

/** The network laid out once per load of /network; every caller shares the same layout. */
export function useLines(): { layout: Layout | null; isLoading: boolean; isError: boolean } {
  const net = useApi<any>("/network");
  const lay = useMemo(() => (net.data ? layout(net.data) : null), [net.data]);
  return { layout: lay, isLoading: net.isLoading, isError: !!net.isError };
}

const pathOf = (pts: [number, number][]) => `M${pts.map(([x, y]) => `${x} ${y}`).join("L")}`;

export interface LineMapProps {
  layout: Layout;
  /** a line's colour; ink when not given */
  lineColor?: (feeder: number) => string | undefined;
  /** a station's fill; its connection status when not given */
  stationColor?: (s: Station) => string | undefined;
  highlight?: number | null;
  onPick?: (cid: number) => void;
  label: string;
}

/**
 * The line map. Stations show their connection status by default; a caller can colour the
 * lines and the stations by anything that has a value per feeder or per house, such as the
 * loading and voltage of the hour on screen.
 */
export const LineMap = memo(function LineMap({ layout: lay, lineColor, stationColor, highlight, onPick, label }: LineMapProps) {
  const t = useT();
  const [hover, setHover] = useState<Station | null>(null);
  const [vx, vy, vw, vh] = lay.box;
  const sel = highlight != null ? lay.byCid.get(highlight) : undefined;
  return (
    <div className="linemap" data-pick={onPick ? "true" : undefined}>
      <svg viewBox={`${vx} ${vy} ${vw} ${vh}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={label}>
        <g className="lm-lines">
          {lay.lines.map((l) => (
            <path
              key={l.feeder}
              d={pathOf(l.points)}
              style={lineColor ? { stroke: lineColor(l.feeder) } : undefined}
              className={sel && sel.line !== l ? "dim" : undefined}
            />
          ))}
        </g>
        <g className="lm-stations">
          {lay.lines.map((l) =>
            l.stations.map((s) => {
              const fill = stationColor?.(s);
              return (
                <circle
                  key={s.cid}
                  cx={s.x}
                  cy={s.y}
                  r={s.cid === highlight ? 9 : 6.5}
                  className={`st ${s.status}${s.cid === highlight ? " sel" : ""}${sel && sel.line !== l ? " dim" : ""}`}
                  style={fill ? { fill } : undefined}
                  onPointerEnter={() => setHover(s)}
                  onPointerLeave={() => setHover((h) => (h?.cid === s.cid ? null : h))}
                  onClick={onPick ? () => onPick(s.cid) : undefined}
                />
              );
            }),
          )}
        </g>
        <g className="lm-hub" aria-hidden="true">
          <circle cx={-8} cy={0} r={13} />
          <circle cx={8} cy={0} r={13} />
        </g>
        <g className="lm-badges" aria-hidden="true">
          {lay.lines.map((l) => (
            <g key={l.feeder} transform={`translate(${l.badge.x} ${l.badge.y})`} className={sel && sel.line !== l ? "dim" : undefined}>
              <rect x={-12} y={-12} width={24} height={24} rx={4} />
              <text y={1}>{l.feeder + 1}</text>
            </g>
          ))}
        </g>
      </svg>
      {hover && (
        <div className="lm-tip" role="status">
          <strong>{hover.address || t("dt.unnamed", { id: hover.cid })}</strong>
          <span>{t("lines.where", { n: (lay.byCid.get(hover.cid)?.line.feeder ?? 0) + 1, m: Math.round(hover.dist) })}</span>
          <span>{t(`twin.status.${hover.status}`)}</span>
          {onPick && <span className="lm-act">{t("lines.test")}</span>}
        </div>
      )}
    </div>
  );
});

/**
 * One feeder as the strip above a train door: every station of the line in order of
 * distance from the substation, the chosen house marked. Its stations are drawn on one
 * scale so the chosen one sits where it sits on the line.
 */
export function LineStrip({ layout: lay, cid }: { layout: Layout; cid: number }) {
  const t = useT();
  const at = lay.byCid.get(cid);
  if (!at) return null;
  const { line, index } = at;
  const n = line.stations.length;
  // as wide as a side panel, so the strip is drawn at about one unit to the pixel
  const W = 400;
  const x0 = 26;
  const step = n > 1 ? (W - x0 - 12) / (n - 1) : 0;
  const me = line.stations[index];
  return (
    <figure className="linestrip">
      <figcaption>
        <span className="ls-badge">{line.feeder + 1}</span>
        <span>{t("lines.strip", { k: index + 1, n })}</span>
        <span className="ls-dist num">{t("lines.metres", { m: Math.round(me.dist) })}</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} 28`} role="img" aria-label={t("lines.stripLabel", { n: line.feeder + 1, k: index + 1, total: n })}>
        <path className="ls-line" d={`M${x0 - 14} 14H${W - 6}`} />
        <g className="lm-hub small">
          <circle cx={6} cy={14} r={6} />
          <circle cx={13} cy={14} r={6} />
        </g>
        {line.stations.map((s, i) => (
          <circle key={s.cid} cx={r1(x0 + i * step)} cy={14} r={i === index ? 7 : 4} className={`st ${s.status}${i === index ? " sel" : ""}`} />
        ))}
      </svg>
    </figure>
  );
}
