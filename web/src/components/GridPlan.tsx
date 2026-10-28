import { useMemo } from "react";
import { useApi } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { Mark } from "./bits";

/** Width of the drawing in its own units; the height follows the network's aspect. */
const W = 1000;
/** One decimal is a tenth of a unit: below anything a screen can show (rendering-svg-precision). */
const r1 = (v: number) => Math.round(v * 10) / 10;

type Lonlat = [number, number];

interface Plan {
  h: number;
  context: string;
  main: string;
  service: string;
  houses: { firm: string; nonfirm: string; none: string };
  cabinets: string;
  trafo: { x: number; y: number } | null;
  scale: { len: number; metres: number };
  mva: number | null;
}

/**
 * Draws the network once into path strings, north up and without tilt: an equirectangular
 * projection around the network's own extent is exact enough at the scale of one
 * neighbourhood. Everything of one kind is a single path, so the plan is a handful of
 * elements however many houses it holds.
 */
function drawPlan(data: any): Plan {
  const feats: any[] = data.features ?? [];
  const own = feats.filter((f) => ["house", "cable", "substation", "cabinet"].includes(f.properties?.layer));
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  const grow = ([lon, lat]: Lonlat) => {
    minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
    minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
  };
  for (const f of own) {
    const g = f.geometry;
    if (g.type === "Point") grow(g.coordinates);
    else if (g.type === "LineString") g.coordinates.forEach(grow);
    else if (g.type === "Polygon") g.coordinates[0].forEach(grow);
  }
  const kx = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
  const wDeg = (maxLon - minLon) * kx;
  const hDeg = maxLat - minLat;
  const pad = 0.07 * Math.max(wDeg, hDeg);
  const s = W / (wDeg + 2 * pad);
  const h = r1((hDeg + 2 * pad) * s);
  const x = (lon: number) => r1(((lon - minLon) * kx + pad) * s);
  const y = (lat: number) => r1((maxLat - lat + pad) * s);
  const ring = (pts: Lonlat[]) => `M${pts.map(([lo, la]) => `${x(lo)} ${y(la)}`).join("L")}Z`;
  const line = (pts: Lonlat[]) => `M${pts.map(([lo, la]) => `${x(lo)} ${y(la)}`).join("L")}`;
  const inView = (pts: Lonlat[]) => pts.some(([lo, la]) => {
    const px = x(lo), py = y(la);
    return px > -20 && px < W + 20 && py > -20 && py < h + 20;
  });

  const context: string[] = [], main: string[] = [], service: string[] = [], cabinets: string[] = [];
  const houses = { firm: [] as string[], nonfirm: [] as string[], none: [] as string[] };
  let trafo: Plan["trafo"] = null;
  let mva: number | null = null;
  for (const f of feats) {
    const p = f.properties ?? {};
    const g = f.geometry;
    if (p.layer === "context" && g.type === "Polygon" && inView(g.coordinates[0])) context.push(ring(g.coordinates[0]));
    else if (p.layer === "cable" && g.type === "LineString") (p.kind === "service" ? service : main).push(line(g.coordinates));
    else if (p.layer === "house" && g.type === "Polygon") {
      const k: keyof typeof houses = p.pv_status === "firm" ? "firm" : p.pv_status === "nonfirm" ? "nonfirm" : "none";
      houses[k].push(ring(g.coordinates[0]));
    } else if (p.layer === "cabinet" && g.type === "Point") {
      const [cx, cy] = [x(g.coordinates[0]), y(g.coordinates[1])];
      cabinets.push(`M${r1(cx - 3)} ${r1(cy - 3)}h6v6h-6Z`);
    } else if (p.layer === "substation" && g.type === "Point") {
      trafo = { x: x(g.coordinates[0]), y: y(g.coordinates[1]) };
      mva = typeof p.trafo_sn_mva === "number" ? p.trafo_sn_mva : null;
    }
  }
  // a 100 m bar: one degree of latitude is 111.32 km, and y runs in latitude
  const metres = 100;
  return {
    h,
    context: context.join(""),
    main: main.join(""),
    service: service.join(""),
    houses: { firm: houses.firm.join(""), nonfirm: houses.nonfirm.join(""), none: houses.none.join("") },
    cabinets: cabinets.join(""),
    trafo,
    scale: { len: r1((metres / 111_320) * s), metres },
    mva,
  };
}

/**
 * The network in plan (design-system.md §5.7): north up, no tilt, cables in ink, houses as
 * outlined footprints coloured by how their solar is connected, the substation as the
 * transformer symbol, and a scale bar — a drawing, not a map widget, so the landing sheet
 * needs no map library.
 */
export function GridPlan() {
  const t = useT();
  const f = useFmt();
  const net = useApi<any>("/network");
  const plan = useMemo(() => (net.data ? drawPlan(net.data) : null), [net.data]);
  return (
    <figure className="plan">
      <figcaption className="plan-head">
        <strong>{t("plan.title")}</strong>
        <span>{t("plan.where")}</span>
      </figcaption>
      {plan ? (
        <svg viewBox={`0 0 ${W} ${plan.h}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={t("overview.mapLabel")}>
          <path className="p-context" d={plan.context} />
          <path className="p-cable p-service" d={plan.service} />
          <path className="p-cable" d={plan.main} />
          <path className="p-house" d={plan.houses.none} />
          <path className="p-house firm" d={plan.houses.firm} />
          <path className="p-house nonfirm" d={plan.houses.nonfirm} />
          <path className="p-cabinet" d={plan.cabinets} />
          {plan.trafo && (
            <g className="p-trafo">
              <circle cx={plan.trafo.x - 4.5} cy={plan.trafo.y} r={7} />
              <circle cx={plan.trafo.x + 4.5} cy={plan.trafo.y} r={7} />
            </g>
          )}
          <g className="p-scale" transform={`translate(28 ${r1(plan.h - 28)})`}>
            <path d={`M0 -5V0H${plan.scale.len}V-5`} />
            <text x={plan.scale.len + 8} y={0}>{plan.scale.metres} m</text>
          </g>
          <g className="p-north" transform={`translate(${W - 34} 34)`}>
            <path d="M0 -14L6 6L0 2L-6 6Z" />
            <text y={22}>N</text>
          </g>
        </svg>
      ) : (
        <div className="plan-empty">{t(net.isError ? "overview.mapUnavailable" : "ui.loading")}</div>
      )}
      <div className="plan-foot">
        <span className="legend-line"><i />{t("plan.cable")} <Mark kind="synthetic" /></span>
        <span className="legend-line"><i className="house" />{t("plan.firm")}</span>
        <span className="legend-line"><i className="house nonfirm" />{t("plan.nonfirm")}</span>
        {plan?.mva != null && <span className="legend-line"><i className="trafo" />{t("plan.trafo", { mva: f(plan.mva, 1) })}</span>}
        <span className="legend-line"><Mark kind="real" />{t("plan.buildings")}</span>
      </div>
    </figure>
  );
}
