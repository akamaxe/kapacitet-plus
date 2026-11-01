import { useMemo } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { DataTable, type Col } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { PageSkeleton } from "../components/Skeleton";
import { SectionBar, type Section } from "../components/SectionBar";
import { ChartOrTable, Missing, Protocol, Tile } from "../components/bits";
import { Term } from "../components/Term";
import { API, useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
import { hourLabel, hoursInYear, monthNames, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

/** A part of the grid as the audit trail names it: transformer, voltage, or "line N" for a cable. */
function elementLabel(t: (k: string) => string, el: string): string {
  return el.startsWith("line ") ? `${t("rep.el.cable")} ${el.slice(5)}` : t(`rep.el.${el}`);
}

interface Agreement {
  customer_id: number;
  address: string;
  hours_limited: number;
  curtailed_kwh: number;
  own_export_kwh: number;
  share_of_own_export_pct: number;
  worst_hour_kwh: number;
}

/**
 * The annual report: what the DSO files with Ei for its conditional connection agreements, and
 * the record behind it, hour by hour. The applicant reads the same record for their own house.
 */
export default function Report() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const cid = useStore((s) => s.selectedCid);
  const select = useStore((s) => s.select);
  const comp = useApi<any>("/compliance");
  const net = useApi<any>("/network");
  const R = comp.data?.data;

  const addresses = useMemo(() => {
    const m = new Map<number, string>();
    (net.data?.features ?? []).forEach((ft: any) => ft.properties?.layer === "house" && m.set(ft.properties.cid, ft.properties.address));
    return m;
  }, [net.data]);
  const rows: Agreement[] = useMemo(
    () => (R?.per_customer ?? []).map((c: any) => ({ ...c, address: addresses.get(c.customer_id) || t("dt.unnamed", { id: c.customer_id }) })),
    [R, addresses, t],
  );

  if (comp.isLoading || net.isLoading) return <PageSkeleton bar hero blocks={2} />;
  if (!R) return <Missing of={[comp, net]} />;

  const H = hoursInYear(R.year);
  const binding = Object.entries(R.binding_constraint_hours ?? {}) as [string, number][];
  const worst = R.worst_uncontrolled;
  const selected = rows.find((r) => r.customer_id === cid) ?? null;
  const cols: Col<Agreement>[] = [
    { key: "address", label: t("rep.col.house"), value: (r) => r.address },
    { key: "hours", label: t("rep.col.hours"), value: (r) => r.hours_limited, numeric: true, unit: "h" },
    { key: "kwh", label: t("rep.col.kwh"), value: (r) => r.curtailed_kwh, cell: (r) => f(r.curtailed_kwh, 1), numeric: true, unit: "kWh" },
    { key: "share", label: t("rep.col.share"), value: (r) => r.share_of_own_export_pct, cell: (r) => f(r.share_of_own_export_pct, 2), numeric: true, unit: "%" },
    { key: "worst", label: t("rep.col.worst"), value: (r) => r.worst_hour_kwh, cell: (r) => f(r.worst_hour_kwh, 2), numeric: true, unit: "kWh" },
  ];
  const sections: Section[] = [
    { id: "resort", label: t("rep.sec.resort") },
    { id: "agreements", label: t("rep.sec.agreements") },
    { id: "house", label: t("rep.sec.house") },
  ];

  return (
    <div className="page">
      <SectionBar sections={sections} basis={<span>{t("rep.basis", { basis: R.reporting_basis, year: R.year })}</span>} />
      <div className="page-inner">
        {/* ---------------------------------------------------------------- the filing */}
        <section className="region">
          <div className="report-hero">
            <div>
              <h1 className="headline">{t("rep.title", { year: R.year })}</h1>
              <p className="headline-sub">{t("rep.lead")}</p>
              <p className="report-figure">
                <span className="rf-value">{f(R.curtailed_share_of_export_pct, 1)}<small>%</small></span>
                <span className="rf-label">{t("rep.figure", { n: f(R.conditional_agreements) })}</span>
              </p>
              <div className="answer-actions">
                <a className="btn" href={`${API}/compliance.txt`} download><Icon name="download" size={15} />{t("rep.dl.filing")}</a>
                <a className="btn ghost" href={`${API}/audit.csv`} download><Icon name="download" size={15} />{t("rep.dl.audit")}</a>
              </div>
            </div>
            <div className="tiles cols-1 answer-sheet">
              <Tile label={t("rep.agreements")} value={f(R.conditional_agreements)} />
              <Tile label={t("rep.hours")} value={f(R.hours_with_curtailment)} unit={t("rep.ofHours", { h: f(H) })} />
              <Tile label={<Term id="curtailment">{t("rep.energy")}</Term>} value={f(R.curtailed_kwh)} unit="kWh" note={t("rep.energyNote", { kwh: f(R.non_firm_export_kwh) })} />
              <Tile label={t("rep.policy")} value={t(`pol.${R.allocation_policy}`)} note={<a href="#/compare">{t("rep.policyNote")}</a>} />
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- last resort */}
        <section className="region" id="resort">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("rep.resort")}</h2>
              <p className="caption">{t("rep.resort.caption")}</p>
            </div>
          </div>
          <Protocol
            rows={[
              { ok: R.verified_violations === 0, label: t("rep.verified"), value: f(R.verified_violations), unit: "h", criterion: t("rep.verifiedNote") },
              ...binding.map(([el, h]) => ({ ok: null, label: t("rep.binding", { el: t(`rep.el.${el}`) }), value: f(h), unit: "h", criterion: t("rep.bindingNote") })),
              { ok: null, label: t("rep.limits"), value: `${f(R.voltage_limits_pu[0], 2)}–${f(R.voltage_limits_pu[1], 2)}`, unit: "pu", criterion: t("rep.limitsNote", { pct: f(R.thermal_limit_pct) }) },
            ]}
          />
          {worst && (
            <div className="report-worst">
              <p className="caption">{t("rep.worst", { when: hourLabel(lang, R.year, worst.hour), el: elementLabel(t, worst.element), pct: f(worst.loading_pct, 0) })}</p>
              <button
                className="btn ghost small"
                onClick={() => {
                  useStore.setState({ hour: worst.hour, hourTouched: true });
                  useStore.getState().setScenario("uncontrolled");
                  useStore.getState().setScreen("twin");
                }}
              >
                {t("rep.worstOpen")}
              </button>
            </div>
          )}
        </section>

        {/* ---------------------------------------------------------------- agreements */}
        <section className="region" id="agreements">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("rep.agreementsTitle")}</h2>
              <p className="caption">{t("rep.agreements.caption")}</p>
            </div>
          </div>
          <DataTable
            rows={rows}
            cols={cols}
            rowKey={(r) => r.customer_id}
            caption={t("rep.agreements.table", { n: rows.length, year: R.year })}
            searchLabel={t("rep.search")}
            selected={cid}
            onSelect={(r) => select(r.customer_id)}
          />
        </section>

        {/* ---------------------------------------------------------------- one house */}
        <section className="region" id="house">
          <div className="region-head">
            <div className="region-title">
              <h2>{selected ? t("rep.houseTitle", { house: selected.address }) : t("rep.house")}</h2>
              <p className="caption">{t("rep.house.caption")}</p>
            </div>
          </div>
          {selected ? (
            <HouseRecord row={selected} year={R.year} />
          ) : (
            <p className="caption-muted">{cid === null ? t("rep.house.pick") : t("rep.house.firm", { house: addresses.get(cid) || t("dt.unnamed", { id: cid }) })}</p>
          )}
        </section>
      </div>
    </div>
  );
}

function HouseRecord({ row, year }: { row: Agreement; year: number }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const env = useApi<any>(`/envelopes/${row.customer_id}`);
  const audit = useApi<any>(`/audit?cid=${row.customer_id}&limit=5000`);
  const grid = useMemo(() => (env.data?.envelope_kw ? minByMonthHour(env.data.envelope_kw, year) : null), [env.data, year]);
  const auditRows: any[] = audit.data?.rows ?? [];
  const cols: Col<any>[] = [
    { key: "hour", label: t("tbl.hour"), value: (r) => r.hour, cell: (r) => hourLabel(lang, year, r.hour) },
    { key: "desired", label: t("rep.col.desired"), value: (r) => r.desired_kw, cell: (r) => f(r.desired_kw, 2), numeric: true, unit: "kW" },
    { key: "granted", label: t("rep.col.granted"), value: (r) => r.granted_kw, cell: (r) => f(r.granted_kw, 2), numeric: true, unit: "kW" },
    { key: "curtailed", label: t("rep.col.curtailed"), value: (r) => r.curtailed_kwh, cell: (r) => f(r.curtailed_kwh, 2), numeric: true, unit: "kWh" },
    { key: "element", label: t("rep.col.element"), value: (r) => elementLabel(t, r.binding_element) },
  ];
  return (
    <div className="stack">
      <div className="tiles cols-3">
        <Tile label={t("rep.col.hours")} value={f(row.hours_limited)} unit="h" />
        <Tile label={t("rep.col.kwh")} value={f(row.curtailed_kwh, 1)} unit="kWh" note={t("rep.house.ofExport", { pct: f(row.share_of_own_export_pct, 2), kwh: f(row.own_export_kwh) })} />
        <Tile label={t("rep.col.worst")} value={f(row.worst_hour_kwh, 2)} unit="kWh" />
      </div>
      {grid && (
        <section className="block stack">
          <div className="block-head"><h3>{t("rep.house.env")}</h3></div>
          <p className="caption">{t("req.envMap.caption", { year })}</p>
          <ChartOrTable
            chart={<Chart ariaLabel={t("rep.house.env")} height={230} deps={[grid, lang]} build={(tk, _w, fm) => envGridOption(tk, grid, lang, t, fm)} />}
            table={
              <div className="table-window">
                <table className="data">
                  <thead><tr><th>{t("tbl.hour")}</th>{monthNames(lang).map((m) => <th key={m} className="r">{m}</th>)}</tr></thead>
                  <tbody>
                    {grid.map((r, h) => (
                      <tr key={h}><td>{String(h).padStart(2, "0")}</td>{r.map((v, m) => <td key={m} className="r">{f(v, 1)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            }
          />
        </section>
      )}
      <section className="block stack">
        <div className="block-head">
          <h3>{t("rep.house.hours", { n: f(auditRows.length) })}</h3>
          <a className="btn ghost small" href={`${API}/audit.csv?cid=${row.customer_id}`} download>
            <Icon name="download" size={15} />{t("rep.dl.house")}
          </a>
        </div>
        {audit.isError ? (
          <Missing of={[audit]} />
        ) : (
          <DataTable rows={auditRows} cols={cols} rowKey={(r) => r.hour} caption={t("rep.house.table", { year })} searchLabel={t("rep.house.search")} />
        )}
      </section>
    </div>
  );
}

/** The lowest export limit in each month and hour of the day, for a year of hourly envelopes. */
function minByMonthHour(env: number[], year: number): number[][] {
  const grid = Array.from({ length: 24 }, () => Array(12).fill(Infinity));
  const start = Date.UTC(year, 0, 1);
  env.forEach((v, h) => {
    const d = new Date(start + h * 3600_000);
    const m = d.getUTCMonth();
    const hh = d.getUTCHours();
    if (v < grid[hh][m]) grid[hh][m] = v;
  });
  return grid.map((r) => r.map((v) => (Number.isFinite(v) ? v : 0)));
}

function envGridOption(tk: Tokens, grid: number[][], lang: string, t: (k: string) => string, f: (v: number | null | undefined, d?: number) => string) {
  const max = Math.max(...grid.flat());
  const data: [number, number, number][] = [];
  // only the cells where the limit came below the system's own capacity, as on the request screen
  for (let h = 0; h < 24; h++) for (let m = 0; m < 12; m++) if (grid[h][m] < max - 0.05) data.push([m, h, grid[h][m]]);
  return {
    grid: { left: 44, right: 70, top: 6, bottom: 26 },
    xAxis: { type: "category", data: monthNames(lang), ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "category", data: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0")), inverse: true, ...axisStyle(tk), splitLine: { show: false }, axisLabel: { color: tk.muted, interval: 3 } },
    visualMap: {
      min: 0, max, orient: "vertical", right: 0, top: "middle", itemHeight: 120, itemWidth: 10,
      textStyle: { color: tk.muted, fontFamily: tk.font }, text: [`${f(max, 1)} kW`, f(0)],
      // one amber ramp: the lower the limit, the stronger the amber (DESIGN.md heatmap palette);
      // an alpha ramp, so a limit just under capacity still shows on the white
      inRange: { color: [tk.warningFill], colorAlpha: [1, 0.3] },
    },
    tooltip: { formatter: (p: any) => `${monthNames(lang)[p.value[0]]} ${String(p.value[1]).padStart(2, "0")}:00<br/>${t("req.tt.lowest")} <b>${f(p.value[2], 1)} kW</b>` },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: tk.surface, borderWidth: 2, borderRadius: 0 } }],
  };
}
