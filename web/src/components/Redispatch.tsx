/**
 * The year's non-market-based redispatch (EU 2019/943, art. 13.4), as `kapacitet redispatch-report`
 * wrote it: always on the day-ahead basis that is issued, whatever basis the rest of the report
 * screen reads, so the toggle above never changes these figures.
 */
import { Icon } from "./Icon";
import { Protocol, SectionMissing, Tile } from "./bits";
import { Term } from "./Term";
import { API, useApi } from "../lib/api";
import { listJoin, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

function component(t: (k: string) => string, el: string): string {
  return el.startsWith("line ") ? `${t("rep.el.cable")} ${el.slice(5)}` : t(`rep.el.${el}`);
}

export function RedispatchSection() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const q = useApi<any>("/redispatch");
  if (q.isLoading) return null;
  const R = q.data?.data;
  if (!R) return <SectionMissing title={t("rep.sec.redispatch")} q={q} />;
  const needed: Record<string, number> = R.binding_components_needed_hours ?? {};
  const MA = R.market_alternatives;
  return (
    <div className="stack">
      <div className="tiles cols-3">
        <Tile label={<Term id="curtailment">{t("rd.energy")}</Term>} value={f(R.curtailed_mwh, 1)} unit="MWh"
          note={t("rd.energyNote", { pct: f(R.share_of_potential_export_pct, 1), mwh: f(R.potential_export_kwh / 1000, 1) })} />
        <Tile label={t("rd.hours")} value={f(R.hours)} unit="h" note={t("rd.hoursNote", { needed: f(R.hours_needed) })} />
        <Tile label={t("rd.installations")} value={f(R.installations)} note={t("rd.installationsNote", { n: f(R.agreements) })} />
        <Tile label={t("rd.compensation")} value={f(R.compensation_sek)} unit="SEK"
          note={t(R.compensation_entered ? "rd.compensationEntered" : "rd.compensationNone")} />
        <Tile label={t("rd.alternatives")} value={f(MA.recorded)}
          note={MA.recorded ? t("rd.alternativesNote", { kinds: listJoin(lang, MA.kinds.map((k: string) => t(`op.market.kind.${k}`).toLowerCase())) }) : <a href="#/operation">{t("rd.alternativesNone")}</a>} />
      </div>
      <Protocol rows={Object.entries(R.binding_components_hours as Record<string, number>).map(([el, h]) => ({
        ok: null, label: t("rd.binding", { el: component(t, el) }), value: f(h), unit: "h",
        criterion: el in needed ? t("rd.bindingNote", { n: f(needed[el]) }) : t("rd.bindingPlain"),
      }))} />
      <div className="answer-actions">
        <a className="btn ghost" href={`${API}/redispatch_sv.txt`} download><Icon name="download" size={15} />{t("rd.dl")}</a>
      </div>
      <p className="caption-muted">{t("rd.note")}</p>
    </div>
  );
}
