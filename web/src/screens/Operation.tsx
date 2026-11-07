import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Chart, axisStyle } from "../components/Chart";
import { DataTable, type Col } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { ChartSkeleton, PageSkeleton } from "../components/Skeleton";
import { SectionBar, type Section } from "../components/SectionBar";
import { ChartOrTable, Chip, Legend, Missing, Protocol, SectionMissing, Status, Tile } from "../components/bits";
import { InboxSection } from "../components/Inbox";
import { CoverNote, MarketAlternatives } from "../components/MarketAlternatives";
import { SealReceipt, useSealedDownload } from "../components/SealedDownload";
import { Term } from "../components/Term";
import { API, ApiError, apiWrite, getJSON, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { RoleNote } from "../components/RoleNote";
import { type Tokens } from "../lib/colors";
import { refusalOf, refusalText } from "../lib/errors";
import { monthLong, monthNames, studyDate, useFmt } from "../lib/format";
import { useT, type TFn } from "../lib/i18n";
import { scrollToInScroller } from "../lib/scroll";
import { useStore } from "../lib/store";

type F = (v: number | null | undefined, d?: number) => string;
const DAY_MS = 86_400_000;

/** A study day (0-based, the study's UTC+1 calendar) as the date an <input type="date"> and the API speak. */
function isoOfDay(year: number, day: number): string {
  return new Date(Date.UTC(year, 0, 1) + day * DAY_MS).toISOString().slice(0, 10);
}

/** A day's results are kept on screen while the next day's arrive, so the section does not blink. */
function useKept<T>(path: string | null) {
  return useQuery<T>({
    queryKey: [path],
    queryFn: () => getJSON<T>(path as string),
    enabled: !!path,
    staleTime: Infinity,
    retry: 1,
    placeholderData: keepPreviousData,
  });
}

/** The same, for a document read as text (the inverter's XML). */
function useText(path: string | null) {
  return useQuery<string>({
    queryKey: ["text", path],
    queryFn: async () => {
      const r = await fetch(`${API}${path}`);
      if (!r.ok) throw new ApiError(path as string, r.status, (await r.text()).slice(0, 600));
      return r.text();
    },
    enabled: !!path,
    staleTime: Infinity,
    retry: 1,
    placeholderData: keepPreviousData,
  });
}

/**
 * Operation: what the DSO issues, delivers and keeps. A house's limits for one day as its inverter
 * would receive them, the season replayed in the shadow against a pilot's two criteria, and a
 * meter checked against what it was sent. Everything here is the day-ahead basis, the one that can
 * be sent; perfect foresight appears only as the comparison it is.
 */
export default function Operation() {
  const t = useT();
  const f = useFmt();
  const cid = useStore((s) => s.selectedCid);
  const select = useStore((s) => s.select);
  const date = useStore((s) => s.opDate);
  const setDate = useStore((s) => s.setOpDate);
  const idx = useApi<any>("/operation/index");
  const net = useApi<any>("/network");
  const shadow = useApi<any>(idx.data ? "/operation/shadow" : null);

  const addresses = useMemo(() => {
    const m = new Map<number, string>();
    for (const ft of net.data?.features ?? []) {
      if (ft.properties?.layer === "house") m.set(ft.properties.cid, ft.properties.address);
    }
    return m;
  }, [net.data]);

  const year: number | undefined = idx.data?.year;
  // a date from a link that is not in the study year is dropped, as an unknown house is
  const dateOk = !!date && !!year && date.startsWith(`${year}-`);
  useEffect(() => {
    if (date && year && !dateOk) setDate(null);
  }, [date, year, dateOk, setDate]);

  if (idx.isLoading || net.isLoading || (idx.data && shadow.isLoading)) return <PageSkeleton bar hero blocks={2} />;
  if (!idx.data || !shadow.data) return <Missing of={[idx, shadow, net]} />;

  const I = idx.data;
  const S = shadow.data;
  const policy: string = S.policy;
  const DA = I.combos.dayahead[policy];
  const FS = I.combos.foresight[policy];
  const agreements: number[] = I.nonfirm_cids;
  const house = cid !== null && agreements.includes(cid) ? cid : agreements[0];
  const name = (c: number) => addresses.get(c) || t("dt.unnamed", { id: c });
  // until a day is chosen, the schedule opens on the season's most curtailed day
  const perDay: number[] = S.bases.dayahead.days.curtailed_kwh;
  const busiest = S.first_day + perDay.indexOf(Math.max(...perDay));
  const day = dateOk ? (date as string) : isoOfDay(year as number, busiest);

  const openDay = (d: number) => {
    setDate(isoOfDay(year as number, d));
    const el = document.getElementById("schedule");
    if (el) scrollToInScroller(el, 72);
  };

  const sections: Section[] = [
    { id: "inbox", label: t("op.sec.inbox") },
    { id: "schedule", label: t("op.sec.schedule") },
    { id: "market", label: t("op.sec.market") },
    { id: "shadow", label: t("op.sec.shadow") },
    { id: "meter", label: t("op.sec.meter") },
  ];

  return (
    <div className="page">
      <SectionBar sections={sections} basis={<span>{t("op.basis", { rule: t(`pol.${policy}`), year: I.year })}</span>} />
      <div className="page-inner">
        {/* ---------------------------------------------------------------- the answer */}
        <section className="region">
          <div className="report-hero">
            <div>
              <h1 className="headline">{t("op.title")}</h1>
              <p className="headline-sub">{t("op.lead")}</p>
              <p className="report-figure">
                <span className="rf-value">{f(DA.curtailed_share_of_export_pct, 1)}<small>%</small></span>
                <span className="rf-label">{t("op.figure", { foresight: f(FS.curtailed_share_of_export_pct, 1) })}</span>
              </p>
              <p className="caption-muted">{t("op.figureNote")}</p>
              {/* the deployment was sized by the perfect-foresight search: issued the day before,
                  the same houses are held back in many more hours (operation/index.json) */}
              {DA.critical_hours != null && FS.critical_hours != null && (
                <p className="caption-muted" data-basis="sized">{t("op.sized", { da: f(DA.critical_hours), fs: f(FS.critical_hours) })}</p>
              )}
            </div>
            <div className="tiles cols-1 answer-sheet">
              <Tile label={<Term id="nonfirm">{t("op.agreements")}</Term>} value={f(agreements.length)} />
              <Tile label={t("op.hoursLimited")} value={f(DA.hours_limited)} unit="h"
                note={t("op.hoursLimitedNote", { needed: f(I.necessary_hours), unneeded: f(DA.activations_unnecessary_hours) })} />
              <Tile label={t("op.violations")} value={f(DA.full_year_violation_hours)} unit="h" note={t("op.violationsNote")} />
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- applications */}
        <section className="region" id="inbox">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("inbox.title")}</h2>
              <p className="caption">{t("inbox.caption")}</p>
            </div>
          </div>
          <InboxSection />
        </section>

        {/* ---------------------------------------------------------------- a day's schedule */}
        <section className="region" id="schedule">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("op.schedule")}</h2>
              <p className="caption">{t("op.schedule.caption")}</p>
            </div>
          </div>
          <div className="report-controls">
            <label className="field-inline">
              <span>{t("op.house")}</span>
              <select value={house} onChange={(e) => select(Number(e.target.value))}>
                {agreements.map((c) => <option key={c} value={c}>{name(c)}</option>)}
              </select>
            </label>
            <label className="field-inline">
              <span>{t("op.date")}</span>
              <input type="date" value={day} min={`${year}-01-01`} max={`${year}-12-31`} required
                onChange={(e) => e.target.value && setDate(e.target.value)} />
            </label>
          </div>
          {cid !== null && cid !== house && (
            <p className="caption-muted">{t("op.notAgreement", { house: name(cid), shown: name(house) })}</p>
          )}
          <DaySchedule cid={house} date={day} />
        </section>

        {/* ---------------------------------------------------------------- the market comes first */}
        <section className="region" id="market">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("op.market")}</h2>
              <p className="caption">{t("op.market.caption")}</p>
            </div>
          </div>
          <MarketAlternatives agreements={agreements} name={name} year={year as number} day={day} />
        </section>

        {/* ---------------------------------------------------------------- the season in the shadow */}
        <section className="region" id="shadow">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("op.shadow")}</h2>
              <p className="caption">{t("op.shadow.caption")}</p>
            </div>
            <Legend items={[
              { color: "var(--warning-fill)", label: t("op.series.dayahead") },
              { color: "var(--series-main)", label: t("op.series.foresight"), line: true },
            ]} />
          </div>
          <Shadow S={S} f={f} onDay={openDay} />
        </section>

        {/* ---------------------------------------------------------------- a meter */}
        <section className="region" id="meter">
          <div className="region-head">
            <div className="region-title">
              <h2>{t("op.meter")}</h2>
              <p className="caption">{t("op.meter.caption")}</p>
            </div>
          </div>
          <MeterCheck cid={house} date={day} name={name(house)} />
        </section>
      </div>
    </div>
  );
}

/* ==================================================================== a day */

function DaySchedule({ cid, date }: { cid: number; date: string }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const q = `cid=${cid}&date=${date}`;
  const sch = useKept<any>(`/operation/schedule?${q}`);
  const xml = useText(`/operation/schedule.xml?${q}`);
  const sealed = useSealedDownload();
  // with sign-in on, issuing to an inverter takes an approver or an admin
  const canIssue = useCan("issue");
  const [reason, setReason] = useState("");
  if (sch.isLoading) return <ChartSkeleton height={240} />;
  if (!sch.data) return <SectionMissing title={t("op.schedule")} q={sch} />;
  const rows: any[] = sch.data.rows;
  const hasDesired = rows.some((r) => r.desired_kw !== null);
  const cut = rows.reduce((s, r) => s + Math.max(0, (r.desired_kw ?? 0) - r.limit_kw), 0);
  const limited = rows.filter((r) => r.desired_kw !== null && r.desired_kw > r.limit_kw + 1e-6).length;
  const cols: Col<any>[] = [
    { key: "study", label: t("op.col.study"), value: (r) => r.study_clock, cell: (r) => r.study_clock.slice(11) },
    { key: "sthlm", label: t("op.col.stockholm"), value: (r) => r.stockholm, cell: (r) => r.stockholm.slice(11) },
    { key: "limit", label: t("op.col.limit"), value: (r) => r.limit_kw, cell: (r) => f(r.limit_kw, 2), numeric: true, unit: "kW" },
    ...(hasDesired ? [{ key: "desired", label: t("op.col.desired"), value: (r: any) => r.desired_kw, cell: (r: any) => f(r.desired_kw, 2), numeric: true, unit: "kW" }] : []),
  ];
  return (
    <div className="stack">
      <div className="tiles cols-3">
        <Tile label={t("op.tile.cut")} value={f(cut, 1)} unit="kWh" note={t("op.tile.cutNote", { n: f(limited) })} />
        <Tile label={t("op.tile.lowest")} value={f(Math.min(...rows.map((r) => r.limit_kw)), 2)} unit="kW" />
        <Tile label={<Term id="fallback">{t("op.tile.fallback")}</Term>} value={f(sch.data.default_kw, 2)} unit="kW" note={t("op.tile.fallbackNote")} />
      </div>
      <Legend items={[
        ...(hasDesired ? [{ color: "var(--series-main)", label: t("op.col.desired"), line: true }] : []),
        { color: "var(--series-dynamic)", label: t("op.col.limit"), line: true },
        ...(hasDesired ? [{ color: "var(--warning-fill)", label: t("op.series.cut") }] : []),
      ]} />
      <ChartOrTable
        chart={<Chart ariaLabel={t("op.chart.day", { date: studyDate(lang, date) })} height={240} deps={[sch.data, lang]} build={(tk, _w, fm) => dayOption(tk, rows, hasDesired, t, fm)} />}
        table={<DataTable rows={rows} cols={cols} rowKey={(r) => r.hour} caption={t("op.table.caption", { date: studyDate(lang, date) })} searchLabel={t("op.table.search")} />}
      />
      <p className="caption-muted">{t("op.clockNote")}</p>
      <section className="block stack" aria-labelledby="op-payload">
        <div className="block-head">
          <h3 id="op-payload">{t("op.payload")}</h3>
        </div>
        <p className="caption">{t("op.payload.caption")}</p>
        <div>
          <div className="answer-actions">
            <button className="btn small" disabled={sealed.busy || !canIssue.ok} title={canIssue.reason} onClick={() => sealed.download("/operation/schedule/issue", { cid, date }, `kapacitet-dercontrols-${cid}-${date}.xml`)}>
              <Icon name="download" size={15} />{t("op.dl.xml")}
            </button>
            <a className="btn ghost small" href={`${API}/operation/default.xml?cid=${cid}`} download><Icon name="download" size={15} />{t("op.dl.default")}</a>
            <a className="btn ghost small" href={`${API}/operation/schedule?${q}&format=csv`} download><Icon name="download" size={15} />CSV</a>
            <a className="btn ghost small" href={`${API}/operation/schedule?${q}`} download={`kapacitet-schedule-${cid}-${date}.json`}><Icon name="download" size={15} />JSON</a>
          </div>
          <RoleNote action="issue" />
          {sealed.refusalCode === "before_market" ? (
            <div className="answer-actions">
              <label className="caption-muted" htmlFor="op-override">{t("op.override.label")}</label>
              <input id="op-override" className="input" type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="btn small" disabled={sealed.busy || !reason.trim() || !canIssue.ok} title={canIssue.reason}
                onClick={() => sealed.download("/operation/schedule/issue", { cid, date, override_reason: reason.trim() }, `kapacitet-dercontrols-${cid}-${date}.xml`)}>
                {t("op.override.issue")}
              </button>
            </div>
          ) : null}
          <CoverNote day={date} />
          <SealReceipt receipt={sealed.receipt} />
        </div>
        <pre className="payload" tabIndex={0} aria-label={t("op.payload")}>{typeof xml.data === "string" ? readable(xml.data) : xml.isError ? t("op.payload.missing") : " "}</pre>
        <p className="caption-muted">{t("op.notice")}</p>
      </section>
    </div>
  );
}

/** The XML one element per line, indented, for reading on screen; the download is the file as sent. */
function readable(raw: string): string {
  let depth = 0;
  const out: string[] = [];
  for (const line of raw.replace(/>\s*</g, ">\n<").split("\n")) {
    const closing = /^<\//.test(line);
    if (closing) depth = Math.max(0, depth - 1);
    out.push("  ".repeat(depth) + line);
    const opening = /^<[^!?/]/.test(line) && !/\/>$/.test(line) && !/<\/[^>]+>$/.test(line);
    if (opening) depth += 1;
  }
  return out.join("\n");
}

function dayOption(tk: Tokens, rows: any[], hasDesired: boolean, t: TFn, f: F) {
  const hours = rows.map((r) => r.study_clock.slice(11, 13));
  const step = "middle" as const;
  const series: any[] = [];
  if (hasDesired) {
    // the curtailed energy is the band between what the house would export and its limit:
    // an invisible base up to the lower of the two, and the gap stacked on it
    series.push(
      { name: "base", type: "line", stack: "gap", step, data: rows.map((r) => Math.min(r.desired_kw ?? 0, r.limit_kw)), symbol: "none",
        lineStyle: { width: 0 }, areaStyle: { opacity: 0 }, silent: true },
      { name: t("op.series.cut"), type: "line", stack: "gap", step, data: rows.map((r) => Math.max(0, (r.desired_kw ?? 0) - r.limit_kw)), symbol: "none",
        lineStyle: { width: 0 }, areaStyle: { color: tk.warningFill, opacity: 0.85 }, silent: true },
      { name: t("op.col.desired"), type: "line", step, data: rows.map((r) => r.desired_kw), symbol: "none",
        lineStyle: { color: tk.main, width: 2 }, itemStyle: { color: tk.main } },
    );
  }
  series.push({ name: t("op.col.limit"), type: "line", step, data: rows.map((r) => r.limit_kw), symbol: "none",
    lineStyle: { color: tk.dynamic, width: 2 }, itemStyle: { color: tk.dynamic } });
  return {
    grid: { left: 48, right: 12, top: 16, bottom: 28 },
    xAxis: { type: "category", data: hours, ...axisStyle(tk), axisLabel: { ...axisStyle(tk).axisLabel, interval: 2 } },
    yAxis: { type: "value", name: "kW", ...axisStyle(tk), splitLine: { show: true, lineStyle: { color: tk.border } } },
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const r = rows[ps[0].dataIndex];
        const lines = ps.filter((p) => p.seriesName !== "base" && p.value !== null && p.value !== undefined)
          .map((p) => `${p.marker}${p.seriesName} <b>${f(p.value, 2)} kW</b>`);
        return [`${r.study_clock.slice(11)} · ${t("op.col.stockholm")} ${r.stockholm.slice(11, 16)}`, ...lines].join("<br/>");
      },
    },
    series,
  };
}

/* ==================================================================== the season */

function Shadow({ S, f, onDay }: { S: any; f: F; onDay: (day: number) => void }) {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const A = S.bases.dayahead;
  const P = S.bases.foresight;
  const months: number[] = S.season_months;
  const span = { from: monthLong(lang, months[0]), to: monthLong(lang, months[1]) };
  const rows = (A.days.curtailed_kwh as number[]).map((v, i) => ({
    day: S.first_day + i,
    date: isoOfDay(S.year, S.first_day + i),
    dayahead: v,
    foresight: P.days.curtailed_kwh[i] as number,
    limited: A.days.hours_limited[i] as number,
    unneeded: A.days.hours_unnecessary_limited[i] as number,
  }));
  type Row = (typeof rows)[number];
  const cols: Col<Row>[] = [
    { key: "date", label: t("op.col.day"), value: (r) => r.date },
    { key: "dayahead", label: t("op.series.dayahead"), value: (r) => r.dayahead, cell: (r) => f(r.dayahead, 1), numeric: true, unit: "kWh" },
    { key: "foresight", label: t("op.series.foresight"), value: (r) => r.foresight, cell: (r) => f(r.foresight, 1), numeric: true, unit: "kWh" },
    { key: "limited", label: t("op.col.limited"), value: (r) => r.limited, cell: (r) => f(r.limited), numeric: true, unit: "h" },
    { key: "unneeded", label: t("op.col.unneeded"), value: (r) => r.unneeded, cell: (r) => f(r.unneeded), numeric: true, unit: "h" },
  ];
  return (
    <div className="stack">
      <p className="lead-answer">
        {A.safety_ok
          ? t(A.go ? "op.go" : "op.nogo", { ...span, within: f(A.within_level), n: f(A.agreements), level: f(S.agreed_level_pct, 1) })
          : t("op.nogo.safety", { ...span, hours: f(A.violation_hours) })}
      </p>
      <Protocol
        rows={[
          { ok: A.safety_ok, label: t("op.crit.safety"), value: f(A.violation_hours), unit: "h",
            criterion: t("op.crit.safetyNote", { days: f(A.season_days) }) },
          { ok: A.level_ok, label: t("op.crit.level"), value: `${f(A.within_level)} / ${f(A.agreements)}`,
            criterion: t("op.crit.levelNote", { level: f(S.agreed_level_pct, 1), median: f(A.median_year_share_pct, 1), worst: f(A.worst_year_share_pct, 1) }) },
          { ok: null, label: t("op.crit.foresight"), value: `${f(P.within_level)} / ${f(P.agreements)}`,
            criterion: t("op.crit.foresightNote", { worst: f(P.worst_year_share_pct, 1) }) },
          // where the real year beat the plan the limits were issued against: audited, never assumed
          ...(A.exceedance
            ? [{ ok: null, label: t("op.crit.exceed"), value: f(A.exceedance.pv_aggregate_hours_over), unit: "h",
                criterion: t("op.crit.exceedNote", {
                  houses: f(A.exceedance.pv_customer_hours_over), kw: f(A.exceedance.pv_worst_customer_kw, 2),
                  load: f(A.exceedance.load_customer_hours_under), year: S.year,
                }) }]
            : []),
          ...(S.persistence && typeof S.persistence.violation_hours === "number"
            // a comparison, not one of the pilot's two tests: measured, never marked Fail beside them
            ? [{ ok: null, label: t("op.crit.persistence"), value: f(S.persistence.violation_hours), unit: "h",
                criterion: t("op.crit.persistenceNote") }]
            : []),
        ]}
      />
      <ChartOrTable
        chart={<Chart ariaLabel={t("op.chart.season", span)} height={220} deps={[S, lang]}
          onClick={(p) => { if (typeof p?.dataIndex === "number") onDay(rows[p.dataIndex].day); }}
          build={(tk, _w, fm) => seasonOption(tk, rows, lang, t, fm)} />}
        table={<DataTable rows={rows} cols={cols} rowKey={(r) => r.day} caption={t("op.shadow.table", span)} searchLabel={t("op.table.searchDay")} onSelect={(r) => onDay(r.day)} />}
      />
      <p className="caption-muted">{t("op.shadow.note")}</p>
    </div>
  );
}

function seasonOption(tk: Tokens, rows: { date: string; dayahead: number; foresight: number; limited: number; unneeded: number }[], lang: string, t: TFn, f: F) {
  const mon = monthNames(lang);
  return {
    grid: { left: 52, right: 12, top: 16, bottom: 28 },
    xAxis: {
      type: "category", data: rows.map((r) => r.date), ...axisStyle(tk),
      // a tick label at each month's first day, as the year strip marks its months
      axisLabel: { ...axisStyle(tk).axisLabel, interval: (_: number, v: string) => v.endsWith("-01"), formatter: (v: string) => mon[Number(v.slice(5, 7)) - 1] },
    },
    yAxis: { type: "value", name: "kWh", ...axisStyle(tk), splitLine: { show: true, lineStyle: { color: tk.border } } },
    tooltip: {
      trigger: "axis",
      formatter: (ps: any[]) => {
        const r = rows[ps[0].dataIndex];
        return [
          studyDate(lang, r.date),
          ...ps.map((p) => `${p.marker}${p.seriesName} <b>${f(p.value, 1)} kWh</b>`),
          t("op.tip.unneeded", { h: f(r.unneeded), of: f(r.limited) }),
          t("op.tip.open"),
        ].join("<br/>");
      },
    },
    series: [
      { name: t("op.series.dayahead"), type: "bar", data: rows.map((r) => r.dayahead), barCategoryGap: "15%", cursor: "pointer",
        itemStyle: { color: tk.warningFill, borderRadius: [2, 2, 0, 0] } },
      { name: t("op.series.foresight"), type: "line", data: rows.map((r) => r.foresight), symbol: "none", cursor: "pointer",
        lineStyle: { color: tk.main, width: 2 }, itemStyle: { color: tk.main } },
    ],
  };
}

/* ==================================================================== a meter */

function MeterCheck({ cid, date, name }: { cid: number; date: string; name: string }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const [res, setRes] = useState<{ body: any; synthetic: boolean; of: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const can = useCan("meter_check");
  const off = busy || !can.ok;
  // a check belongs to its house: another house's readings are not this house's result
  useEffect(() => {
    setRes(null);
    setError(null);
  }, [cid]);

  const run = async (csv: string, synthetic: boolean, of: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await apiWrite("/operation/meter-check", { cid, csv });
      const body = await r.json().catch(() => null);
      if (!r.ok) {
        setRes(null);
        setError(refusalText(t, refusalOf(body), String(r.status)));
      } else setRes({ body, synthetic, of });
    } catch {
      setError(t("ui.offline"));
    } finally {
      setBusy(false);
    }
  };
  const example = async () => {
    setBusy(true);
    setError(null);
    const r = await fetch(`${API}/operation/meter-example?cid=${cid}&date=${date}`).catch(() => null);
    if (!r?.ok) {
      setBusy(false);
      setError(r ? refusalText(t, refusalOf(await r.json().catch(() => null)), String(r.status)) : t("ui.offline"));
      return;
    }
    await run(await r.text(), true, t("op.meter.exampleOf", { house: name, date: studyDate(lang, date) }));
  };
  const fromFile = async (input: HTMLInputElement) => {
    const file = input.files?.[0];
    input.value = ""; // the same file chosen again is a new check
    if (file) await run(await file.text(), false, file.name);
  };

  const R = res?.body;
  const cols: Col<any>[] = [
    { key: "t", label: t("op.col.reading"), value: (r) => r.timestamp },
    { key: "e", label: t("op.col.export"), value: (r) => r.export_w, cell: (r) => f(r.export_w), numeric: true, unit: "W" },
    { key: "l", label: t("op.col.limitW"), value: (r) => r.limit_w, cell: (r) => f(r.limit_w), numeric: true, unit: "W" },
    { key: "x", label: t("op.col.excess"), value: (r) => r.excess_w, cell: (r) => f(r.excess_w), numeric: true, unit: "W" },
  ];
  return (
    <div className="stack">
      <div className="answer-actions">
        <button className="btn small" disabled={off} title={can.reason} onClick={example}>{t("op.meter.example")}</button>
        <label className="btn ghost small" aria-disabled={off || undefined} title={can.reason}>
          <Icon name="upload" size={15} />{t("op.meter.file")}
          <input type="file" accept=".csv,text/csv" className="sr-only" disabled={off} onChange={(e) => void fromFile(e.currentTarget)} />
        </label>
      </div>
      <RoleNote action="meter_check" />
      <p className="caption-muted">{t("op.meter.format")}</p>
      {error && <p className="caption" role="alert"><Status kind="critical">{error}</Status></p>}
      {res && R && (
        <div className="stack" aria-live="polite">
          <p className="caption">{res.of}{res.synthetic && <> · <Chip kind="synthetic" /></>}</p>
          <div className="tiles cols-4">
            <Tile label={t("op.meter.readings")} value={f(R.readings)} />
            <Tile label={t("op.meter.breaches")} value={f(R.breaches)} tone={R.breaches > 0 ? "danger" : undefined}
              note={t("op.meter.tolerance", { w: f(R.tolerance_w) })} />
            <Tile label={t("op.meter.compliance")} value={f(R.compliance_pct, 2)} unit="%" />
            <Tile label={t("op.meter.maxExcess")} value={f(Math.max(0, R.max_excess_w ?? 0))} unit="W" />
          </div>
          {R.breach_rows.length > 0 && (
            <DataTable rows={R.breach_rows} cols={cols} rowKey={(r: any) => r.timestamp} caption={t("op.meter.table", { house: name })} searchLabel={t("op.meter.search")} />
          )}
        </div>
      )}
    </div>
  );
}
