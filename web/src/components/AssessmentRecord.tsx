import { createPortal } from "react-dom";
import { Roundel } from "./Shell";
import { dateTime, pct1, useFmt, monthNames } from "../lib/format";
import { useT } from "../lib/i18n";
import { reasonText } from "../lib/reasons";
import { useStore } from "../lib/store";

/**
 * The printable record of one assessment: what a DSO files and what an applicant keeps.
 *
 * It lives outside the app root (a portal on <body>) and is shown only in print, so Ctrl+P or
 * "Print or save as PDF" on the request screen produces this sheet instead of the map and the
 * panel. Every figure is read from the saved answer; the sheet adds only labels and units.
 */
export function AssessmentRecord({ res, baselineLabel }: { res: any; baselineLabel: string }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const c = res.customer ?? {};
  const rq = res.request ?? {};
  const sum = res.summary ?? {};
  const prof = res.profiles ?? {};
  const year = prof.year ?? res.verification?.year;
  const created = res.created ? new Date(res.created) : null;
  const when = created ? dateTime(lang, created) : "–";
  const runAt = res.provenance?.run_id ? new Date(res.provenance.run_id) : null;
  const run = runAt && !Number.isNaN(runAt.getTime()) ? dateTime(lang, runAt) : res.provenance?.run_id ?? "–";
  const yesNo = (ok: boolean) => (ok ? t("req.approved") : t("req.rejected"));
  const priceYears: number[] = (res.economics?.by_year ?? []).map((e: any) => e.year).sort((a: number, b: number) => a - b);
  const weatherYears: number[] = (res.risk?.by_year ?? []).map((e: any) => e.year);
  const grid: number[][] | undefined = prof.envelope_min_month_hour;
  const months = monthNames(lang);

  return createPortal(
    <article className="print-record" lang={lang} aria-hidden="true">
      <header className="pr-head">
        <p className="pr-brand"><Roundel />Kapacitet+</p>
        <h1>{t("rec.title")}</h1>
        <dl className="pr-meta">
          <dt>{t("rec.id")}</dt><dd>{res.id ?? "–"}</dd>
          <dt>{t("rec.created")}</dt><dd>{when}</dd>
          <dt>{t("rec.engine")}</dt><dd>{res.version ?? "–"}</dd>
          <dt>{t("rec.run")}</dt><dd>{run}</dd>
        </dl>
      </header>

      <section>
        <h2>{t("rec.house")}</h2>
        <dl className="pr-kv">
          <dt>{t("rec.address")}</dt><dd>{c.address || t("dt.unnamed", { id: c.cid })} ({t("rec.houseNo", { id: c.cid })})</dd>
          <dt>{t("req.roof")}</dt><dd>{f(c.roof_kwp, 1)} kWp</dd>
          <dt>{t("req.fuse")}</dt><dd>{c.fuse_a} A ({f(c.fuse_kva, 1)} kVA)</dd>
          <dt>{t("req.feeder")}</dt><dd>{(c.feeder ?? 0) + 1}</dd>
          <dt>{t("req.distance")}</dt><dd>{f(c.dist_m)} m</dd>
        </dl>
      </section>

      <section>
        <h2>{t("rec.request")}</h2>
        <dl className="pr-kv">
          <dt>{t("req.pv")}</dt><dd>{rq.pv_kwp > 0 ? `${f(rq.pv_kwp, 1)} kWp` : t("req.none")}</dd>
          <dt>{t("req.ev")}</dt><dd>{rq.ev_kw > 0 ? `${f(rq.ev_kw, 1)} kW` : t("req.none")}</dd>
          {rq.hp !== undefined && <><dt>{t("req.hp")}</dt><dd>{rq.hp ? t("rec.yes") : t("req.none")}</dd></>}
          {rq.bat_kwh !== undefined && <><dt>{t("req.bat")}</dt><dd>{rq.bat_kwh > 0 ? `${f(rq.bat_kwh, 1)} kWh` : t("req.none")}</dd></>}
          <dt>{t("req.baseline")}</dt><dd>{baselineLabel}</dd>
        </dl>
      </section>

      <section>
        <h2>{t("rec.answer")}</h2>
        <table className="pr-table">
          <tbody>
            {res.static?.pv && (
              <tr><th>{t("rec.pvStatic")}</th><td>{yesNo(res.static.pv.approved)}</td><td>{reasonText(t, f, res.static.pv)}</td></tr>
            )}
            {rq.pv_kwp > 0 && (
              <tr>
                <th>{t("rec.pvDoe")}</th>
                <td>{sum.pv_curtailment_ref_pct > 0.005 ? t("req.approvedLimits") : t("req.approved")}</td>
                <td>{t("rec.pvDoeDetail", { z: f(sum.pv_curtailment_ref_pct, 1), year, h: f(sum.pv_hours_limited_ref) })}</td>
              </tr>
            )}
            {res.verification?.hours_checked !== undefined && (
              <tr>
                <th>{t("rec.verified")}</th>
                <td>{f(res.verification.violations_after)} {t("rec.violations")}</td>
                <td>{res.verification.hours_checked > 0
                  ? t("rec.verifiedDetail", { n: f(res.verification.hours_checked), year: res.verification.year })
                  : t("req.verifiedNone")}</td>
              </tr>
            )}
            {res.static?.ev && (
              <tr><th>{t("rec.evStatic")}</th><td>{yesNo(res.static.ev.approved)}</td><td>{reasonText(t, f, res.static.ev)}</td></tr>
            )}
            {rq.ev_kw > 0 && (
              <tr><th>{t("rec.evDoe")}</th><td>{pct1(f, sum.ev_unmet_pct)} %</td><td>{t("rec.evDoeDetail", { h: f(sum.ev_hours_limited_ref), year })}</td></tr>
            )}
            {res.static?.hp && (
              <tr><th>{t("rec.hpStatic")}</th><td>{yesNo(res.static.hp.approved)}</td><td>{reasonText(t, f, res.static.hp)}</td></tr>
            )}
            {res.static?.hp && (
              <tr><th>{t("rec.hpDoe")}</th><td>{pct1(f, sum.hp_unmet_pct)} %</td><td>{t("rec.hpDoeDetail", { h: f(sum.hp_hours_limited_ref), year, kwh: f(sum.hp_kwh_mean) })}</td></tr>
            )}
            {sum.bat_charged_ref_kwh !== undefined && (
              <tr><th>{t("rec.bat")}</th><td>{sum.bat_captured_ref_pct == null ? "–" : `${pct1(f, sum.bat_captured_ref_pct)} %`}</td><td>{t("rec.batDetail", { kwh: f(sum.bat_discharged_ref_kwh), year, z: f(sum.curtailment_after_battery_ref_pct, 1) })}</td></tr>
            )}
            {res.verification?.import && (
              <tr><th>{t("rec.importVerified")}</th><td>{f(res.verification.import.violations_after)} {t("rec.violations")}</td><td>{t("rec.importVerifiedDetail", { n: f(res.verification.import.hours_checked) })}</td></tr>
            )}
          </tbody>
        </table>
      </section>

      {grid && (
        <section className="pr-break">
          <h2>{t("rec.envTable", { year })}</h2>
          <p className="pr-note">{t("rec.envTableNote", { cap: f(prof.cap_kw, 1) })}</p>
          <table className="pr-table pr-grid">
            <thead>
              <tr><th>{t("tbl.hour")}</th>{months.map((m) => <th key={m}>{m}</th>)}</tr>
            </thead>
            <tbody>
              {grid.map((row, h) => (
                <tr key={h}><th>{String(h).padStart(2, "0")}</th>{row.map((v, m) => <td key={m}>{f(v, 1)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {res.risk?.mean_pct !== undefined && (
        <section>
          <h2>{t("req.risk")}</h2>
          <dl className="pr-kv">
            <dt>{t("cmp.risk.mean")}</dt><dd>{pct1(f, res.risk.mean_pct)} %</dd>
            <dt>{t("cmp.risk.p90")}</dt><dd>{pct1(f, res.risk.p90_pct)} %</dd>
            <dt>{t("cmp.risk.cvar")}</dt><dd>{pct1(f, res.risk.cvar95_pct)} %</dd>
            <dt>{t("rec.yearsRange")}</dt><dd>{pct1(f, res.risk.best_year_pct)} – {pct1(f, res.risk.worst_year_pct)} %</dd>
          </dl>
          <p className="pr-note">{t("rec.riskNote", { n: weatherYears.length, from: weatherYears[0] ?? "–", to: weatherYears[weatherYears.length - 1] ?? "–" })}</p>
        </section>
      )}

      {res.economics?.by_year && (
        <section>
          <h2>{t("req.money")}</h2>
          <dl className="pr-kv">
            <dt>{t("req.money.curtailed")}</dt><dd>{f(res.economics.curtailed_sek_mean)} {t("unit.sekYr")}</dd>
            <dt>{t("req.money.export")}</dt><dd>{f(res.economics.export_sek_mean)} {t("unit.sekYr")}</dd>
            <dt>{t("req.money.self")}</dt><dd>{f(res.economics.self_use_spot_sek_mean)} {t("unit.sekYr")}</dd>
          </dl>
          <p className="pr-note">{t("req.money.note", { from: priceYears[0] ?? "–", to: priceYears[priceYears.length - 1] ?? "–" })}</p>
        </section>
      )}

      <footer className="pr-foot">
        <h2>{t("rec.basis")}</h2>
        <p>{t("rec.real")}</p>
        <p>{t("rec.synthetic")}</p>
        <ul>{(res.provenance?.sources ?? []).map((s: string) => <li key={s}>{s}</li>)}</ul>
        <p>{t("rec.method")}</p>
      </footer>
    </article>,
    document.body,
  );
}
