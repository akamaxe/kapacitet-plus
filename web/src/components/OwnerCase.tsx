/**
 * The house's side of the business case: connect now with the envelope, or wait for the
 * reinforcement, valued at retail prices (engine/economics.owner_case). No chart, so the
 * applicant's portal can show it without loading the chart library.
 */
import { Tile } from "./bits";
import { useApi } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

/** SEK to the nearest hundred: the inputs do not support more. */
export function sekRounded(f: (v: number | null | undefined, d?: number) => string, v: number): string {
  return f(Math.round(v / 100) * 100);
}

/** The figures of an owner case, whoever fetched it; `compact` stacks the three readings for the
 * request screen's narrow panel. */
export function OwnerFigures({ O, headingId = "owner-title", compact = false }: { O: any; headingId?: string; compact?: boolean }) {
  const t = useT();
  const f = useFmt();
  const span = O.price_years.length ? `${O.price_years[0]}–${O.price_years[O.price_years.length - 1]}` : "";
  const ore = (v: number) => f(v * 100, 1);
  return (
    <section className="block stack" aria-labelledby={headingId}>
      <div className="block-head"><h3 id={headingId}>{t("own.title")}</h3></div>
      <p className="lead-answer">{t("own.lead", { years: f(O.years), now: sekRounded(f, O.connect_now_sek) })}</p>
      <div className={`tiles ${compact ? "cols-1" : "cols-3"}`}>
        <Tile label={t("own.self")} value={sekRounded(f, O.per_year.self_use_sek)} unit={t("unit.sekYr")}
          note={t("own.selfNote", { kwh: f(O.kwh.self_use + O.kwh.battery), tax: ore(O.retail_sek_kwh.energy_tax), fee: ore(O.retail_sek_kwh.transfer_fee), vat: f(O.retail_sek_kwh.vat * 100) })} />
        <Tile label={t("own.export")} value={sekRounded(f, O.per_year.export_sek)} unit={t("unit.sekYr")}
          note={t("own.exportNote", { kwh: f(O.kwh.export), rate: ore(O.feed_in_sek_kwh) })} />
        <Tile label={t("own.curtailed")} value={sekRounded(f, O.per_year.curtailed_sek)} unit={t("unit.sekYr")}
          note={t("own.curtailedNote", { kwh: f(O.kwh.curtailed) })} />
      </div>
      <p className="caption-muted">{t("own.note", { rate: f(O.rate_pct, 1), undisc: sekRounded(f, O.undiscounted_sek), last: O.tax_reduction.last_year, span })}</p>
    </section>
  );
}

/** Under an answer with solar on the request screen. */
export function OwnerCase({ id }: { id: string }) {
  const q = useApi<any>(`/requests/${id}/owner-case`);
  return q.data ? <OwnerFigures O={q.data} compact /> : null;
}
