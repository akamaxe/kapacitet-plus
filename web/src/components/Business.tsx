/**
 * The business case (api: /business, /requests/{id}/owner-case).
 *
 * `Deferral` is the DSO's side, on the comparison screen: what deferring the reinforcement is worth
 * at Ei's real rate, net of what the envelopes' curtailment costs the houses, for every horizon.
 * `OwnerCase` is the house's side, under an answer: connect now with the envelope, or wait for the
 * reinforcement, valued at retail prices.
 */
import { Chart, axisStyle } from "./Chart";
import { ChartOrTable, Legend, Tile } from "./bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { useFmt } from "../lib/format";
import { useT, type TFn } from "../lib/i18n";
import { useStore } from "../lib/store";

type F = (v: number | null | undefined, d?: number) => string;

/** SEK to the nearest hundred: the inputs do not support more. */
const sek = (f: F, v: number) => f(Math.round(v / 100) * 100);
const ksek = (f: F, v: number) => f(v / 1000);

export function Deferral() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const q = useApi<any>("/business");
  const B = q.data?.data;
  if (!B) return null;
  const [lo, hi] = B.at_horizon.transformer;
  const span = B.price_years.length ? `${B.price_years[0]}–${B.price_years[B.price_years.length - 1]}` : "";
  return (
    <section className="block stack" aria-labelledby="deferral-title">
      <div className="block-head">
        <h3 id="deferral-title">{t("biz.title")}</h3>
        <Legend items={[
          { color: "var(--series-dynamic)", label: t("biz.series.net") },
          { color: "var(--warning-fill)", label: t("biz.series.curtailment"), line: true },
        ]} />
      </div>
      <p className="lead-answer">{t("biz.lead", { years: f(B.horizon_years), lo: ksek(f, lo.net_sek), hi: ksek(f, hi.net_sek), rate: f(B.rate_pct, 2) })}</p>
      <ChartOrTable
        chart={<Chart ariaLabel={t("biz.title")} height={240} deps={[B, lang]} build={(tk, _w, fm) => deferralOption(tk, B, t, fm)} />}
        table={
          <table className="data">
            <caption className="sr-only">{t("biz.table")}</caption>
            <thead><tr><th>{t("biz.col.years")}</th><th className="r">{t("biz.col.deferral")}</th><th className="r">{t("biz.col.curtailment")}</th><th className="r">{t("biz.col.net")}</th></tr></thead>
            <tbody>
              {B.curves.low.map((p: any, i: number) => (
                <tr key={p.years}>
                  <td>{f(p.years)}</td>
                  <td className="r">{ksek(f, p.deferral_sek)}–{ksek(f, B.curves.high[i].deferral_sek)}</td>
                  <td className="r">{ksek(f, p.curtailment_sek)}</td>
                  <td className="r">{ksek(f, p.net_sek)}–{ksek(f, B.curves.high[i].net_sek)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
      />
      <p className="caption">{t("biz.always", { perp: ksek(f, B.perpetual_curtailment_sek), k: f(B.curtailed_sek_per_year) })}</p>
      <p className="caption-muted">{t("biz.note", { year: B.price_level_year, span, slo: ksek(f, B.at_horizon.with_station[0].net_sek), shi: ksek(f, B.at_horizon.with_station[1].net_sek) })}</p>
    </section>
  );
}

function deferralOption(tk: Tokens, B: any, t: TFn, f: F) {
  const years = B.curves.low.map((p: any) => p.years);
  const low = B.curves.low.map((p: any) => p.net_sek / 1000);
  const gap = B.curves.high.map((p: any, i: number) => p.net_sek / 1000 - low[i]);
  const cost = B.curves.low.map((p: any) => p.curtailment_sek / 1000);
  return {
    grid: { left: 52, right: 16, top: 16, bottom: 30 },
    xAxis: { type: "category", data: years, name: t("biz.axis.years"), nameLocation: "middle", nameGap: 22, ...axisStyle(tk), boundaryGap: false },
    yAxis: { type: "value", name: "kSEK", ...axisStyle(tk), splitLine: { show: true, lineStyle: { color: tk.border } } },
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const i = ps[0].dataIndex;
        return [
          t("biz.tip.years", { n: f(years[i]) }),
          `${t("biz.series.net")} <b>${f(low[i], 0)}–${f(low[i] + gap[i], 0)} kSEK</b>`,
          `${t("biz.series.curtailment")} <b>${f(cost[i], 0)} kSEK</b>`,
        ].join("<br/>");
      },
    },
    series: [
      { name: "low", type: "line", stack: "net", data: low, symbol: "none", lineStyle: { color: tk.dynamic, width: 2 }, silent: true,
        markLine: { symbol: "none", silent: true, lineStyle: { color: tk.muted, type: "dashed", width: 1 },
          label: { color: tk.text2, fontFamily: tk.font, fontSize: tk.size.caption, formatter: t("biz.horizon", { n: B.horizon_years }) },
          data: [{ xAxis: B.horizon_years }] } },
      { name: t("biz.series.net"), type: "line", stack: "net", data: gap, symbol: "none", lineStyle: { color: tk.dynamic, width: 2 },
        areaStyle: { color: tk.goodQuiet, opacity: 0.8 } },
      { name: t("biz.series.curtailment"), type: "line", data: cost, symbol: "none", lineStyle: { color: tk.warningFill, width: 2 } },
    ],
  };
}

/** Under an answer with solar: what connecting now is worth to the house against waiting. */
export function OwnerCase({ id }: { id: string }) {
  const t = useT();
  const f = useFmt();
  const q = useApi<any>(`/requests/${id}/owner-case`);
  const O = q.data;
  if (!O) return null;
  const span = O.price_years.length ? `${O.price_years[0]}–${O.price_years[O.price_years.length - 1]}` : "";
  const ore = (v: number) => f(v * 100, 1);
  return (
    <section className="block stack" aria-labelledby="owner-title">
      <div className="block-head"><h3 id="owner-title">{t("own.title")}</h3></div>
      <p className="lead-answer">{t("own.lead", { years: f(O.years), now: sek(f, O.connect_now_sek) })}</p>
      <div className="tiles cols-3">
        <Tile label={t("own.self")} value={sek(f, O.per_year.self_use_sek)} unit={t("unit.sekYr")}
          note={t("own.selfNote", { kwh: f(O.kwh.self_use + O.kwh.battery), tax: ore(O.retail_sek_kwh.energy_tax), fee: ore(O.retail_sek_kwh.transfer_fee), vat: f(O.retail_sek_kwh.vat * 100) })} />
        <Tile label={t("own.export")} value={sek(f, O.per_year.export_sek)} unit={t("unit.sekYr")}
          note={t("own.exportNote", { kwh: f(O.kwh.export), rate: ore(O.feed_in_sek_kwh) })} />
        <Tile label={t("own.curtailed")} value={sek(f, O.per_year.curtailed_sek)} unit={t("unit.sekYr")}
          note={t("own.curtailedNote", { kwh: f(O.kwh.curtailed) })} />
      </div>
      <p className="caption-muted">{t("own.note", { rate: f(O.rate_pct, 1), undisc: sek(f, O.undiscounted_sek), last: O.tax_reduction.last_year, span })}</p>
    </section>
  );
}
