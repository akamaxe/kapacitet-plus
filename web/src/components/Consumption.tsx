/**
 * The consumption capacity, next to the generation capacity: how many new EV chargers, heat pumps
 * or constant loads the street takes, by the hourly study and by the static study's convention,
 * and how much each house alone can add. Every figure is read from results/consumption.json.
 */
import { CommandLine, Tile } from "./bits";
import { Icon } from "./Icon";
import { consumptionFigures, type ConsumptionDoc, type Device } from "../lib/consumption";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

export function ConsumptionRegion({ c }: { c: ConsumptionDoc | undefined }) {
  const t = useT();
  const f = useFmt();
  if (!c) {
    return (
      <section className="region" id="consumption">
        <div className="region-head"><div className="region-title"><h2>{t("cons.title")}</h2></div></div>
        <div className="section-missing" role="note">
          <p className="caption">{t("cons.missing")}</p>
          <CommandLine command="python -m kapacitet compute --stage extra --only consumption" />
        </div>
      </section>
    );
  }
  const k = consumptionFigures(c);
  const kw = f(k.ev.device_kw ?? 0);
  const range = (d: Device) => t("cons.range", { lo: f(d.adopters.p10, 1), hi: f(d.adopters.p90, 1), now: f(d.existing) });
  return (
    <section className="region" id="consumption">
      <div className="region-head">
        <div className="region-title">
          <h2>{t("cons.title")}</h2>
          <p className="caption">{t("cons.lead", { years: k.span })}</p>
        </div>
      </div>
      {c.baseline_violates ? (
        <p className="decision-error" role="status"><Icon name="alert" size={15} />{t("cons.baseline", { h: f(k.baselineHours) })}</p>
      ) : (
        <>
          <div className="tiles cols-3">
            <Tile label={t("cons.ev", { kw })} value={f(k.ev.adopters.median, 1)} unit={t("cons.unit.chargers")} note={range(k.ev)} />
            <Tile label={t("cons.hp")} value={f(k.hp.adopters.median, 1)} unit={t("cons.unit.hp")} note={range(k.hp)} />
            <Tile label={t("cons.flat", { kw })} value={f(k.flat.adopters.median, 1)} unit={t("cons.unit.chargers")}
              note={t("cons.flatNote", { kw })} />
          </div>
          {k.ratio !== null && (
            <p className="cons-ratio">{t("cons.ratio", { x: f(k.ratio, 1), ev: f(k.ev.adopters.median, 1), flat: f(k.flat.adopters.median, 1) })}</p>
          )}
          <p className="caption">{t("cons.nodal", {
            ts: f(k.nodal.timeseries.median, 1), lo: f(k.nodal.timeseries.p10, 1), hi: f(k.nodal.timeseries.p90, 1),
            st: f(k.nodal.static.median, 1),
          })}</p>
          <table className="data">
            <caption className="sr-only">{t("cons.title")}</caption>
            <thead>
              <tr>
                <th>{t("cons.tbl.device")}</th><th className="r">{t("cons.tbl.median")}</th><th className="r">P10</th>
                <th className="r">P90</th><th className="r">{t("cons.tbl.minmax")}</th><th className="r">{t("cons.tbl.today")}</th>
                <th className="r">{t("cons.tbl.candidates")}</th>
              </tr>
            </thead>
            <tbody>
              {(["ev", "hp", "flat"] as const).map((key) => {
                const d = c.devices[key];
                return (
                  <tr key={key}>
                    <td>{t(`cons.row.${key}`, { kw })}</td>
                    <td className="r">{f(d.adopters.median, 1)}</td><td className="r">{f(d.adopters.p10, 1)}</td>
                    <td className="r">{f(d.adopters.p90, 1)}</td><td className="r">{f(d.adopters.min)}–{f(d.adopters.max)}</td>
                    <td className="r">{f(d.existing)}</td><td className="r">{f(d.candidates)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      <p className="source-line">{t("cons.source", { n: f(c.n_orders), years: f(c.years.length) })}</p>
    </section>
  );
}
