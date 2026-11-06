import { createPortal } from "react-dom";
import { Roundel } from "./Shell";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

/**
 * The printable annual filing: the figures a DSO sends, on its basis and under its sharing rule,
 * with every agreement listed. Like the assessment record it is a portal on <body> shown only in
 * print, so printing the report screen gives this sheet instead of a blank page.
 */
export function ReportRecord({ R, rows }: {
  R: any;
  rows: { customer_id: number; address: string; hours_limited: number; curtailed_kwh: number; share_of_own_export_pct: number }[];
}) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  // the hours a limit was needed, as on the screen (binding_constraint_necessary_hours)
  const binding = Object.entries(R.binding_constraint_necessary_hours ?? R.binding_constraint_hours ?? {}) as [string, number][];
  const dayahead = R.basis === "dayahead";
  return createPortal(
    <article className="print-record filing" lang={lang} aria-hidden="true">
      <header className="pr-head">
        <p className="pr-brand"><Roundel />Kapacitet+</p>
        <h1>{t("rec.reportTitle", { year: R.year })}</h1>
        <dl className="pr-meta">
          <dt>{t("rep.basisLabel")}</dt><dd>{t(`rep.basis.${R.basis ?? "foresight"}`)} · {R.reporting_basis_code ?? ""}</dd>
          <dt>{t("rep.rule")}</dt><dd>{t(`pol.${R.allocation_policy}`)}</dd>
          <dt>{t("rec.generated")}</dt><dd>{dateTime(lang, new Date())}</dd>
        </dl>
      </header>

      <section>
        <h2>{t("rep.title", { year: R.year })}</h2>
        <dl className="pr-kv">
          <dt>{t("rep.agreements")}</dt><dd>{f(R.conditional_agreements)}</dd>
          <dt>{t("rep.hours")}</dt><dd>{f(R.hours_with_curtailment)} h</dd>
          <dt>{t("rep.energy")}</dt><dd>{f(R.curtailed_kwh)} kWh ({f(R.curtailed_share_of_export_pct, 2)} %)</dd>
          <dt>{t("rep.verified")}</dt><dd>{f(R.verified_violations)} h</dd>
          {dayahead && R.activations_necessary_hours !== undefined && (
            <><dt>{t("rep.necessary")}</dt><dd>{f(R.activations_necessary_hours)} / {f(R.hours_with_curtailment)} h</dd></>
          )}
        </dl>
        <p className="pr-note">{t(dayahead ? "rep.basis.dayaheadNote" : "rep.basis.foresightNote")}</p>
      </section>

      <section>
        <h2>{t(dayahead ? "rep.resortDayahead" : "rep.resort")}</h2>
        <table className="pr-table">
          <tbody>
            {binding.map(([el, h]) => (
              <tr key={el}><th>{t("rep.binding", { el: t(`rep.el.${el}`) })}</th><td>{f(h)} h</td></tr>
            ))}
            <tr><th>{t("rep.limits")}</th><td>{f(R.voltage_limits_pu[0], 2)}–{f(R.voltage_limits_pu[1], 2)} pu · {f(R.thermal_limit_pct)} %</td></tr>
          </tbody>
        </table>
      </section>

      <section className="pr-break">
        <h2>{t("rec.reportAgreements")}</h2>
        <table className="pr-table">
          <thead>
            <tr><th>{t("rep.col.house")}</th><th>{t("rep.col.hours")}</th><th>{t("rep.col.kwh")}</th><th>{t("rep.col.share")}</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.customer_id}>
                <td>{r.address}</td><td>{f(r.hours_limited)}</td><td>{f(r.curtailed_kwh, 1)}</td><td>{f(r.share_of_own_export_pct, 2)} %</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <footer className="pr-foot">
        <h2>{t("rec.basis")}</h2>
        <p>{t("rec.real")}</p>
        <p>{t("rec.synthetic")}</p>
        <p>{t("rec.method")}</p>
      </footer>
    </article>,
    document.body,
  );
}
