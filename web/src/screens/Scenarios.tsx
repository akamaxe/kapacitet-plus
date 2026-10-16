import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../components/Chart";
import { ChartOrTable, Loading, Missing, Status, Tile } from "../components/bits";
import { useApi } from "../lib/api";
import { type Tokens } from "../lib/colors";
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

  if (sc.isLoading) return <Loading />;
  if (!D) return <Missing />;

  const pvs: number[] = D.pv_shares;
  const evs: number[] = D.ev_shares;
  const cur = sel ? cells.find((c) => c.pv === sel.pv && c.ev === sel.ev) : cells.find((c) => c.pv === pvs[Math.floor(pvs.length / 2)] && c.ev === evs[Math.floor(evs.length / 2)]);
  const pct = (x: number) => `${Math.round(x * 100)} %`;

  // breaking points at the lowest EV share
  const row0 = cells.filter((c) => c.ev === evs[0]).sort((a, b) => a.pv - b.pv);
  const lastStatic = [...row0].reverse().find((c) => c.static_ok);
  const lastDoe = [...row0].reverse().find((c) => c.doe_ok);
  const firstStaticFail = row0.find((c) => !c.static_ok);

  return (
    <div className="page">
      <div className="page-inner">
        <section className="stack" style={{ gap: 14 }}>
          <h1 className="headline" style={{ fontSize: "var(--text-h1)", maxWidth: "34ch" }}>{t("scn.title")}</h1>
          <p className="headline-sub">{t("scn.sub", { year: D.year })}</p>
          {firstStaticFail && lastDoe && (
            <p className="caption">{t("scn.breaking", { pv: Math.round(firstStaticFail.pv * 100), pv2: Math.round(lastDoe.pv * 100) })}</p>
          )}
          <div className="row">
            <span className="caption">{t("scn.hp")}</span>
            <div className="seg">
              {D.hp_shares.map((h: number) => (
                <button key={h} aria-pressed={Math.abs(h - hpNow) < 1e-9} onClick={() => setHp(h)}>{pct(h)}</button>
              ))}
            </div>
          </div>
        </section>

        <div className="grid-2" style={{ gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr)" }}>
          <section className="block">
            <div className="row" style={{ gap: 18, marginBottom: 6 }}>
              <Status kind="good">{t("scn.both")}</Status>
              <Status kind="warning">{t("scn.doeOnly")}</Status>
              <Status kind="critical">{t("scn.none")}</Status>
            </div>
            <ChartOrTable
              chart={
                <Chart
                  ariaLabel={t("scn.title")}
                  height={380}
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
                    {cells.map((c) => (
                      <tr key={`${c.pv}-${c.ev}`}><td>{pct(c.pv)}</td><td>{pct(c.ev)}</td><td>{c.static_ok ? t("scn.ok") : t("scn.nok")}</td><td>{c.doe_ok ? t("scn.ok") : t("scn.nok")}</td><td className="r">{f(c.doe_z_pct, 2)} %</td><td className="r">{f(c.ev_unmet_pct, 2)} %</td></tr>
                    ))}
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
                  <span className="v-method">{t("scn.staticMethod")}</span>
                  {cur.static_ok ? <Status kind="good">{t("scn.ok")}</Status> : <Status kind="critical">{t("scn.nok")}</Status>}
                </div>
                <div className="verdict">
                  <span className="v-method">{t("scn.doeMethod")}</span>
                  {cur.doe_ok ? <Status kind="good">{t("scn.ok")}</Status> : <Status kind="critical">{t("scn.nok")}</Status>}
                </div>
              </div>
              <div className="tiles">
                <Tile label={t("scn.peak")} value={f(cur.peak_load_kw)} unit="kW" />
                <Tile label={t("scn.z")} value={f(cur.doe_z_pct, 2)} unit="%" note={`≤ ${D.z_star_pct} %`} />
                <Tile label={t("scn.expH")} value={f(cur.uncontrolled_export_hours)} unit="h" />
                <Tile label={t("scn.impH")} value={f(cur.uncontrolled_import_hours)} unit="h" />
                <Tile label={t("scn.unmet")} value={f(cur.ev_unmet_pct, 2)} unit="%" note={`≤ ${D.ev_unmet_limit_pct} %`} />
                <Tile label={t("scn.firm")} value={f(cur.firm_import_violation_hours)} unit="h" />
              </div>
              {lastStatic && <p className="caption-muted">{t("scn.staticUpTo", { pv: pct(lastStatic.pv), ev: pct(evs[0]) })}</p>}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function matrixOption(tk: Tokens, cells: Cell[], pvs: number[], evs: number[], t: (k: string) => string, sel: { pv: number; ev: number } | null) {
  const colors = [tk.accent, tk.warning, tk.danger];
  const glyph = ["✓", "◐", "✕"];
  const data = cells.map((c) => {
    const s = stateOf(c);
    const isSel = sel && sel.pv === c.pv && sel.ev === c.ev;
    return {
      value: [pvs.indexOf(c.pv), evs.indexOf(c.ev), s],
      itemStyle: { color: colors[s], borderColor: isSel ? tk.text : tk.surface, borderWidth: isSel ? 4 : 3 },
      label: { formatter: glyph[s], color: s === 1 ? "#102530" : "#ffffff" },
    };
  });
  return {
    grid: { left: 70, right: 16, top: 10, bottom: 50 },
    xAxis: { type: "category", data: pvs.map((p) => `${Math.round(p * 100)} %`), name: t("scn.pv"), nameLocation: "middle", nameGap: 32, ...axisStyle(tk), splitLine: { show: false } },
    yAxis: { type: "category", data: evs.map((e) => `${Math.round(e * 100)} %`), name: t("scn.ev"), nameLocation: "middle", nameGap: 50, ...axisStyle(tk), splitLine: { show: false } },
    tooltip: {
      formatter: (p: any) => {
        const c = cells.find((x) => pvs.indexOf(x.pv) === p.value[0] && evs.indexOf(x.ev) === p.value[1]);
        if (!c) return "";
        return `${t("scn.pv")}: ${Math.round(c.pv * 100)} %, ${t("scn.ev")}: ${Math.round(c.ev * 100)} %<br/>${t("scn.staticMethod")}: <b>${c.static_ok ? t("scn.ok") : t("scn.nok")}</b><br/>${t("scn.doeMethod")}: <b>${c.doe_ok ? t("scn.ok") : t("scn.nok")}</b>`;
      },
    },
    series: [{
      type: "heatmap", data,
      label: { show: true, fontSize: 16, fontWeight: 700 },
      emphasis: { itemStyle: { borderColor: tk.text, borderWidth: 3 } },
    }],
  };
}
