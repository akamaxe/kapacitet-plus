/**
 * A project's facts as a data sheet: what it is, its network, the capacity of the static and the
 * hourly study, the weather years and when it was computed. The start window shows it for the
 * project picked in the list, the project's properties for the one that is open.
 */
import { Tile } from "./bits";
import { Icon } from "./Icon";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { assumptionText, type Project } from "../lib/projects";
import { useStore } from "../lib/store";

/** A network is synthetic when it was made from an example network, or its project file says so. */
export function isSynthetic(p: Project): boolean {
  return p.origin.kind === "template" || p.origin.synthetic === true;
}

/** What the project is, in one sentence under its name. */
export function useKind() {
  const t = useT();
  return (p: Project): string => {
    if (p.reference) return t("proj.kind.reference");
    if (p.origin.kind === "template") return t("proj.kind.template", { name: p.origin.name ?? p.origin.template ?? "" });
    if (p.origin.kind === "project_file") return t(p.origin.synthetic ? "proj.kind.fileSynthetic" : "proj.kind.file");
    return t("proj.kind.csv");
  };
}

/** A project's sources: its own site's weather and prices, or the reference site's, and the households. */
function SiteFacts({ p }: { p: Project }) {
  const t = useT();
  const f = useFmt();
  const s = p.summary.site;
  const span = (ys: number[]) => (ys.length ? (ys.length === 1 ? String(ys[0]) : `${Math.min(...ys)}–${Math.max(...ys)}`) : "–");
  return (
    <dl className="kv proj-site">
      <dt>{t("proj.site.weather")}</dt>
      <dd>{s ? t("proj.site.weatherOwn", { name: s.name, lat: f(s.lat, 3), lon: f(s.lon, 3), station: s.station.name,
        id: s.station.id, km: f(s.station.distance_km, 1) }) : t("proj.site.reference")}</dd>
      <dt>{t("proj.site.prices")}</dt>
      <dd>{s ? t("proj.site.pricesOwn", { zone: s.prices.zone, years: span(s.prices.years) }) : t("proj.site.reference")}</dd>
      <dt>{t("proj.site.households")}</dt>
      <dd>{t("proj.site.householdsPool")}</dd>
    </dl>
  );
}

export function ProjectFacts({ p }: { p: Project }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const years = p.summary.years;
  const yearsText = years ? (years[0] === years[1] ? String(years[0]) : `${years[0]}–${years[1]}`) : "–";
  return (
    <>
      <div className="tiles">
        <Tile
          label={t("proj.d.network")}
          value={p.customers === null ? "–" : <span className="t-word">{
            p.summary.feeders === null
              ? t("proj.d.customers", { n: f(p.customers) })
              : t("proj.d.networkValue", { n: f(p.customers), feeders: f(p.summary.feeders) })
          }</span>}
        />
        <Tile label={t("proj.d.trafo")} value={<span className="t-word">{p.summary.trafo_type ?? "–"}</span>} />
        <Tile
          label={t("proj.d.static")}
          value={p.summary.static_kw === null ? "–" : f(p.summary.static_kw)}
          unit={p.summary.static_kw === null ? undefined : "kW"}
        />
        <Tile
          label={t("proj.d.hourly")}
          value={p.summary.hourly_kw === null ? "–" : f(p.summary.hourly_kw)}
          unit={p.summary.hourly_kw === null ? undefined : "kW"}
        />
        {p.summary.ev_chargers !== null && (
          <Tile
            label={t("proj.d.chargers")}
            value={f(p.summary.ev_chargers, 1)}
            note={p.summary.ev_chargers_static === null ? undefined : t("proj.d.chargersNote", { n: f(p.summary.ev_chargers_static, 1) })}
          />
        )}
        <Tile
          label={t("proj.d.years")}
          mark={years ? "real" : undefined}
          value={<span className="t-word">{yearsText}</span>}
          note={p.quick ? t("proj.d.quickNote") : undefined}
        />
        <Tile
          label={t("proj.d.computed")}
          value={<span className="t-word">{p.summary.computed_at ? dateTime(lang, p.summary.computed_at) : "–"}</span>}
        />
      </div>
      {(p.summary.static_kw !== null || p.summary.hourly_kw !== null) && (
        <p className="source-line">{t("proj.d.capacityNote")}</p>
      )}
      {/* where its weather, prices and households come from: the reference project's are its own */}
      {!p.reference && p.state === "ready" && <SiteFacts p={p} />}
      {p.summary.assumptions.length > 0 && (
        <div className="proj-assume">
          <h3>{t("proj.assume.title")}</h3>
          <ul>
            {p.summary.assumptions.map((a) => (
              <li key={`${a.code}${a.label ?? ""}`} data-weighty={a.weighty || undefined}>
                {a.weighty && <Icon name="alert" size={15} />}
                <span>{assumptionText(t, f, a)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
