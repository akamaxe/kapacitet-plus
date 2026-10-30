import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { PageSkeleton } from "../components/Skeleton";
import { ChartOrTable, Missing, Status, Tile } from "../components/bits";
import { Term } from "../components/Term";

/** The three outcomes of a cell, each with its own drawn mark: the same marks in the key,
    the matrix and the table, so a state never depends on its colour alone. */
const STATES = ["good", "warning", "critical"] as const;
function CellKey({ state, children }: { state: 0 | 1 | 2; children: React.ReactNode }) {
  return <span className={`cell-key ${STATES[state]}`}><i aria-hidden="true" />{children}</span>;
}
import { useApi } from "../lib/api";
import type { Tokens } from "../lib/colors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

type Cell = any;

function stateOf(c: Cell): 0 | 1 | 2 {
  if (c.static_ok) return 0;
  if (c.doe_ok) return 1;
  return 2;
}

export default function Scenarios() {
  const t = useT();
  const f = useFmt();
  const sc = useApi<any>("/scenarios");
  const D = sc.data?.data;
  const [hp, setHp] = useState<number | null>(null);
  const [sel, setSel] = useState<{ pv: number; ev: number } | null>(null);

  const hpNow = hp ?? D?.hp_shares?.[0] ?? 0.3;
  const cells: Cell[] = useMemo(() => (D ? D.cases.filter((c: Cell) => Math.abs(c.hp - hpNow) < 1e-9) : []), [D, hpNow]);

  if (sc.isLoading) return <PageSkeleton blocks={2} />;
  if (!D) return <Missing of={[sc]} />;

  const pvs: number[] = D.pv_shares;
  const evs: number[] = D.ev_shares;
  const cur = sel ? cells.find((c) => c.pv === sel.pv && c.ev === sel.ev) : cells.find((c) => c.pv === pvs[Math.floor(pvs.length / 2)] && c.ev === evs[Math.floor(evs.length / 2)]);
  const pct = (x: number) => `${Math.round(x * 100)} %`;

  // breaking points at the lowest EV share
  const row0 = cells.filter((c) => c.ev === evs[0]).sort((a, b) => a.pv - b.pv);
  const lastDoe = [...row0].reverse().find((c) => c.doe_ok);
  const firstStaticFail = row0.find((c) => !c.static_ok);

  const pick = (c: Cell) => setSel({ pv: c.pv, ev: c.ev });

  return (
    <div className="page">
      <div className="page-inner">
        <section className="region">
          <h1 className="headline">{t("scn.title")}</h1>
          {/* the screen's answer, said first and at the size of an answer */}
          {firstStaticFail && lastDoe && (
            <p className="lead-answer">{t("scn.breaking", { pv: Math.round(firstStaticFail.pv * 100), pv2: Math.round(lastDoe.pv * 100) })}</p>
          )}
          <p className="caption">{t("scn.sub", { year: D.year })}</p>
        </section>

        <div className="grid-2 wide-first">
          <section className="block">
            <div className="block-head">
              <div className="row legend-row">
                <CellKey state={0}>{t("scn.both")}</CellKey>
                <CellKey state={1}>{t("scn.doeOnly")}</CellKey>
                <CellKey state={2}>{t("scn.none")}</CellKey>
              </div>
              <div className="row">
                <span className="caption" id="hp-label">{t("scn.hp")}</span>
                <div className="seg small" role="group" aria-labelledby="hp-label">
                  {D.hp_shares.map((h: number) => (
                    <button key={h} aria-pressed={Math.abs(h - hpNow) < 1e-9} onClick={() => setHp(h)}>{pct(h)}</button>
                  ))}
                </div>
              </div>
            </div>
            <ChartOrTable
              chart={
                <Chart
                  ariaLabel={t("scn.title")}
                  height={330}
                  deps={[cells, cur]}
                  build={(tk) => matrixOption(tk, cells, pvs, evs, t, cur ? { pv: cur.pv, ev: cur.ev } : null)}
                  onClick={(p: any) => {
                    if (p?.value) setSel({ pv: pvs[p.value[0]], ev: evs[p.value[1]] });
                  }}
                />
              }
              table={
                <table className="data">
                  <thead><tr><th>{t("scn.pv")}</th><th>{t("scn.ev")}</th><th>{t("scn.staticMethod")}</th><th>{t("scn.doeMethod")}</th><th className="r">{t("scn.z")}</th><th className="r">{t("scn.unmet")}</th></tr></thead>
                  <tbody>
                    {cells.map((c) => {
                      const on = !!cur && cur.pv === c.pv && cur.ev === c.ev;
                      return (
                        // a row picks its cell, so the matrix can be worked from the keyboard too
                        <tr
                          key={`${c.pv}-${c.ev}`}
                          tabIndex={0}
                          aria-selected={on}
                          className={on ? "picked" : undefined}
                          onClick={() => pick(c)}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(c); } }}
                        >
                          <td>{pct(c.pv)}</td><td>{pct(c.ev)}</td>
                          <td><CellKey state={c.static_ok ? 0 : 2}>{c.static_ok ? t("scn.ok") : t("scn.nok")}</CellKey></td>
                          <td><CellKey state={c.doe_ok ? 0 : 2}>{c.doe_ok ? t("scn.ok") : t("scn.nok")}</CellKey></td>
                          <td className="r">{f(c.doe_z_pct, 2)} %</td><td className="r">{f(c.ev_unmet_pct, 2)} %</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              }
            />
          </section>

          {cur && (
            <section className="block stack">
              <h2>{t("scn.cell")}</h2>
              <p className="caption">{t("scn.pv")}: <b>{pct(cur.pv)}</b>, {t("scn.ev")}: <b>{pct(cur.ev)}</b>, {t("scn.hp")}: <b>{pct(cur.hp)}</b></p>
              <div className="verdicts">
                <div className="verdict">
                  <span className="v-method"><Term id="static">{t("scn.staticMethod")}</Term></span>
                  {cur.static_ok ? <Status kind="good">{t("scn.ok")}</Status> : <Status kind="critical">{t("scn.nok")}</Status>}
                </div>
                <div className="verdict">
                  <span className="v-method"><Term id="envelope">{t("scn.doeMethod")}</Term></span>
                  {cur.doe_ok ? <Status kind="good">{t("scn.ok")}</Status> : <Status kind="critical">{t("scn.nok")}</Status>}
                </div>
              </div>
              <div className="tiles cols-1">
                <Tile label={t("scn.peak")} value={f(cur.peak_load_kw)} unit="kW" />
                <Tile label={t("scn.z")} value={f(cur.doe_z_pct, 2)} unit="%" note={`≤ ${D.z_star_pct} %`} />
                <Tile label={t("scn.expH")} value={f(cur.uncontrolled_export_hours)} unit="h" />
                <Tile label={t("scn.impH")} value={f(cur.uncontrolled_import_hours)} unit="h" />
                <Tile label={t("scn.unmet")} value={f(cur.ev_unmet_pct, 2)} unit="%" note={`≤ ${D.ev_unmet_limit_pct} %`} />
                <Tile label={t("scn.firm")} value={f(cur.firm_import_violation_hours)} unit="h" />
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function matrixOption(tk: Tokens, cells: Cell[], pvs: number[], evs: number[], t: (k: string) => string, sel: { pv: number; ev: number } | null) {
  // a wash for the cell and a drawn mark in the full colour: forty cells of solid green,
  // amber and red would outshout the one the reader picked
  const fills = [tk.goodQuiet, tk.warningQuiet, tk.dangerQuiet];
  const inks = [tk.good, tk.warning, tk.danger];
  // the same marks as the key: a disc, a triangle, a cross
  const marks = ["circle", "triangle", "path://M2 0L6 4L10 0L12 2L8 6L12 10L10 12L6 8L2 12L0 10L4 6L0 2Z"];
  let selAt = -1;
  const data = cells.map((c, i) => {
    const s = stateOf(c);
    const isSel = sel && sel.pv === c.pv && sel.ev === c.ev;
    if (isSel) selAt = i;
    return {
      value: [pvs.indexOf(c.pv), evs.indexOf(c.ev), s],
      itemStyle: { color: fills[s], borderColor: isSel ? tk.accent : tk.surface, borderWidth: isSel ? 4 : 3 },
    };
  });
  // cells are drawn in order and each one's gap covers its neighbours' edges, so the
  // selected cell goes last or its outline loses two sides
  if (selAt >= 0) data.push(...data.splice(selAt, 1));
  const markData = cells.map((c) => {
    const s = stateOf(c);
    return { value: [pvs.indexOf(c.pv), evs.indexOf(c.ev)], symbol: marks[s], itemStyle: { color: inks[s] } };
  });
  const tip = (p: any) => {
    const c = cells.find((x) => pvs.indexOf(x.pv) === p.value[0] && evs.indexOf(x.ev) === p.value[1]);
    if (!c) return "";
    return `${t("scn.pv")}: ${Math.round(c.pv * 100)} %, ${t("scn.ev")}: ${Math.round(c.ev * 100)} %<br/>${t("scn.staticMethod")}: <b>${c.static_ok ? t("scn.ok") : t("scn.nok")}</b><br/>${t("scn.doeMethod")}: <b>${c.doe_ok ? t("scn.ok") : t("scn.nok")}</b>`;
  };
  return {
    grid: { left: 70, right: 16, top: 10, bottom: 50 },
    xAxis: { type: "category", data: pvs.map((p) => `${Math.round(p * 100)} %`), name: t("scn.pv"), nameLocation: "middle", nameGap: 32, ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "category", data: evs.map((e) => `${Math.round(e * 100)} %`), name: t("scn.ev"), nameLocation: "middle", nameGap: 50, ...axisStyle(tk), splitLine: { show: false } },
    tooltip: { formatter: tip },
    series: [
      {
        type: "heatmap", data,
        emphasis: { itemStyle: { borderColor: tk.text, borderWidth: 3 } },
      },
      { type: "scatter", data: markData, symbolSize: 11, silent: true, z: 3 },
    ],
  };
}
