/**
 * The market alternatives the DSO consulted before signing an agreement or before a period of
 * limits (Ei2025:01 §2.3; EU 2019/943, art. 13): a form that records one consultation, sealed in
 * the evidence chain, and the list of what is on record. A day no record covers can still be
 * issued; its record carries a warning, and `CoverNote` says so beside the issue button.
 */
import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, type Col } from "./DataTable";
import { Icon } from "./Icon";
import { SectionMissing } from "./bits";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { responseRefusal } from "../lib/errors";
import { dateTime, studyDate, useFmt } from "../lib/format";
import { useT, type TFn } from "../lib/i18n";
import { useStore } from "../lib/store";

const PATH = "/operation/market-alternatives";
const SCOPES = ["activation_period", "agreement", "zone"] as const;
const KINDS = ["local_flexibility_market", "bilateral_flexibility", "network_tariff", "none_available", "other"] as const;
type Scope = (typeof SCOPES)[number];

interface Alt { kind: string; name: string; consulted_at: string; outcome: string; note: string }
export interface MarketRecord {
  id: string;
  recorded_at: string;
  scope: Scope;
  ref: { cid?: number; start?: string; end?: string };
  alternatives: Alt[];
  decided_by: string;
  seal: { digest: string; seq: number; at: string } | null;
}

const blank = (day: string): Alt => ({ kind: "local_flexibility_market", name: "", consulted_at: day, outcome: "", note: "" });

/** The records whose period covers `day` (ISO dates compare as text). */
export function covering(records: MarketRecord[], day: string): MarketRecord[] {
  return records.filter((r) => r.scope !== "agreement" && !!r.ref.start && !!r.ref.end && r.ref.start <= day && day <= r.ref.end);
}

function about(t: TFn, lang: string, r: MarketRecord, name: (cid: number) => string): string {
  if (r.scope === "agreement" && r.ref.cid !== undefined) return `${t("op.market.scope.agreement")}: ${name(r.ref.cid)}`;
  const period = t("op.market.period", { start: studyDate(lang, r.ref.start ?? ""), end: studyDate(lang, r.ref.end ?? "") });
  return `${t(`op.market.scope.${r.scope}`)}: ${period}`;
}

export function MarketAlternatives({ agreements, name, year, day }: {
  agreements: number[]; name: (cid: number) => string; year: number; day: string;
}) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const list = useApi<{ entries: MarketRecord[]; market_result_local: string }>(PATH);
  const [scope, setScope] = useState<Scope>("activation_period");
  const [cid, setCid] = useState<number>(agreements[0]);
  const [start, setStart] = useState(day);
  const [end, setEnd] = useState(day);
  const [alts, setAlts] = useState<Alt[]>([blank(day)]);
  const [by, setBy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = alts.every((a) => !!a.consulted_at) && (scope === "agreement" || (!!start && !!end && start <= end));
  const edit = (i: number, patch: Partial<Alt>) => setAlts((xs) => xs.map((a, j) => (j === i ? { ...a, ...patch } : a)));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const ref = scope === "agreement" ? { cid } : { start, end };
    const body = { scope, ref, alternatives: alts.map((a) => ({ ...a, name: a.name.trim(), outcome: a.outcome.trim(), note: a.note.trim() })), decided_by: by.trim() };
    // one key per submission: a retry of this request records once
    const r = await apiWrite(PATH, body, "POST", { "Idempotency-Key": crypto.randomUUID() }).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    const out = await r.json();
    await client.invalidateQueries({ queryKey: [PATH] });
    toast.info(t("op.market.done", { digest: String(out.seal?.digest ?? "").slice(0, 12) }));
    setAlts([blank(day)]);
  };

  const records = list.data?.entries ?? [];
  const cols: Col<MarketRecord>[] = [
    { key: "at", label: t("op.market.col.at"), value: (r) => r.recorded_at, cell: (r) => dateTime(lang, r.recorded_at) },
    { key: "for", label: t("op.market.col.for"), value: (r) => about(t, lang, r, name) },
    {
      key: "alts", label: t("op.market.col.alts"),
      value: (r) => r.alternatives.map((a) => `${t(`op.market.kind.${a.kind}`)} ${a.name} ${a.outcome}`).join(" "),
      cell: (r) => (
        <ul className="market-alts">
          {r.alternatives.map((a, i) => (
            <li key={i}>
              <strong>{t(`op.market.kind.${a.kind}`)}</strong>{a.name ? ` · ${a.name}` : ""} · {studyDate(lang, a.consulted_at)}
              {a.outcome ? <span className="caption-muted"> · {a.outcome}</span> : null}
            </li>
          ))}
        </ul>
      ),
    },
    { key: "by", label: t("op.market.col.by"), value: (r) => r.decided_by || "–" },
    { key: "seal", label: t("op.market.col.seal"), value: (r) => r.seal?.digest ?? "", cell: (r) => (r.seal ? <code>{r.seal.digest.slice(0, 12)}…</code> : "–") },
  ];

  return (
    <div className="stack">
      {list.data && <p className="caption-muted">{t("op.market.gate", { local: list.data.market_result_local })}</p>}
      <form className="block stack market-form" aria-labelledby="market-form" onSubmit={save}>
        <div className="block-head"><h3 id="market-form">{t("op.market.form")}</h3></div>
        <div className="seg" role="group" aria-label={t("op.market.scope")}>
          {SCOPES.map((s) => (
            <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>{t(`op.market.scope.${s}`)}</button>
          ))}
        </div>
        {scope === "agreement" ? (
          <div className="field decision-note">
            <label htmlFor="market-house">{t("op.market.house")}</label>
            <select id="market-house" className="market-select" value={cid} onChange={(e) => setCid(Number(e.target.value))}>
              {agreements.map((c) => <option key={c} value={c}>{name(c)}</option>)}
            </select>
          </div>
        ) : (
          <div className="sign-pair">
            <div className="field decision-note">
              <label htmlFor="market-start">{t("op.market.start")}</label>
              <input id="market-start" type="date" required min={`${year}-01-01`} max={`${year}-12-31`} value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div className="field decision-note">
              <label htmlFor="market-end">{t("op.market.end")}</label>
              <input id="market-end" type="date" required min={start || `${year}-01-01`} max={`${year}-12-31`} value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
        )}
        {alts.map((a, i) => (
          <fieldset key={i} className="market-alt">
            <legend>{t("op.market.alt", { n: f(i + 1) })}</legend>
            <div className="market-alt-grid">
            <div className="field decision-note">
              <label htmlFor={`market-kind-${i}`}>{t("op.market.kind")}</label>
              <select id={`market-kind-${i}`} className="market-select" value={a.kind} onChange={(e) => edit(i, { kind: e.target.value })}>
                {KINDS.map((k) => <option key={k} value={k}>{t(`op.market.kind.${k}`)}</option>)}
              </select>
            </div>
            <div className="field decision-note">
              <label htmlFor={`market-name-${i}`}>{t("op.market.name")}</label>
              <input id={`market-name-${i}`} maxLength={200} value={a.name} onChange={(e) => edit(i, { name: e.target.value })} />
            </div>
            <div className="field decision-note">
              <label htmlFor={`market-when-${i}`}>{t("op.market.consulted")}</label>
              <input id={`market-when-${i}`} type="date" required value={a.consulted_at} onChange={(e) => edit(i, { consulted_at: e.target.value })} />
            </div>
            <div className="field decision-note">
              <label htmlFor={`market-outcome-${i}`}>{t("op.market.outcome")}</label>
              <input id={`market-outcome-${i}`} maxLength={500} value={a.outcome} onChange={(e) => edit(i, { outcome: e.target.value })} />
            </div>
            <div className="field decision-note market-alt-note">
              <label htmlFor={`market-note-${i}`}>{t("op.market.note")}</label>
              <textarea id={`market-note-${i}`} rows={2} maxLength={2000} value={a.note} onChange={(e) => edit(i, { note: e.target.value })} />
            </div>
            </div>
            {alts.length > 1 && (
              <button type="button" className="btn ghost small market-alt-remove" onClick={() => setAlts((xs) => xs.filter((_, j) => j !== i))}>
                {t("op.market.remove")}
              </button>
            )}
          </fieldset>
        ))}
        {alts.length < 20 && (
          <div>
            <button type="button" className="btn ghost small" onClick={() => setAlts((xs) => [...xs, blank(day)])}>
              {t("op.market.add")}
            </button>
          </div>
        )}
        <div className="field decision-note">
          <label htmlFor="market-by">{t("op.market.by")}</label>
          <input id="market-by" maxLength={120} autoComplete="organization-title" value={by} onChange={(e) => setBy(e.target.value)} />
        </div>
        {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
        <div className="decision-actions">
          <button type="submit" className="btn" disabled={!ready || busy}>{busy ? t("req.decide.working") : t("op.market.go")}</button>
        </div>
      </form>
      {list.isError ? (
        <SectionMissing title={t("op.market")} q={list} />
      ) : records.length ? (
        <DataTable rows={records} cols={cols} rowKey={(r) => r.id}
          caption={t("op.market.table", { n: f(records.length) })} searchLabel={t("op.market.search")} />
      ) : (
        <p className="caption-muted">{t("op.market.none")}</p>
      )}
    </div>
  );
}

/** Beside the issue button: whether the day's activations have market alternatives on record. */
export function CoverNote({ day }: { day: string }) {
  const t = useT();
  const f = useFmt();
  const list = useApi<{ entries: MarketRecord[] }>(PATH);
  if (!list.data) return null;
  const n = covering(list.data.entries, day).length;
  return <p className="caption-muted" data-market-cover={n}>{n ? t("op.market.covered", { n: f(n) }) : t("op.market.uncovered")}</p>;
}
