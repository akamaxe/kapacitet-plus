/**
 * A project's facts as a data sheet: what it is, its network, the capacity of the static and the
 * hourly study, the weather years and when it was computed. The start window shows it for the
 * project picked in the list, the project's properties for the one that is open.
 */
import { Tile } from "./bits";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import type { Project } from "../lib/projects";
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
    </>
  );
}
