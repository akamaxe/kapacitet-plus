/**
 * The evidence pack for Ei (GET /eipack): each of Ei's conditions for conditional agreements
 * (Ei2025:01) beside what the prototype can show for it, the three things only the DSO can state,
 * a sealed download, and a printable description of the method. Evidence, never a legal conclusion.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { SealReceipt, useSealedDownload } from "./SealedDownload";
import { Roundel } from "./Shell";
import { Status } from "./bits";
import { toast } from "./Toast";
import { API, useApi } from "../lib/api";
import { responseRefusal } from "../lib/errors";
import { dateTime, listJoin, useFmt } from "../lib/format";
import { useT, type TFn } from "../lib/i18n";
import { useStore } from "../lib/store";

type F = (v: number | null | undefined, d?: number) => string;

const KIND = { evidence: "good", not_met: "critical" } as const;

function labelOf(c: any, lang: string): string {
  return (lang === "sv" ? c.label_sv : c.label) || c.label;
}

/** A condition's facts, in the reader's language and format. */
function factLine(t: TFn, f: F, fact: { code: string; params: Record<string, any> }): string {
  const p: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(fact.params ?? {})) {
    if (k === "rule") p[k] = t(`pol.${v}`);
    else if (k === "lo" || k === "hi") p[k] = f((v as number) / 1000);
    else if (typeof v === "number") p[k] = f(v);
    else p[k] = v;
  }
  return t(`ei.fact.${fact.code}`, p);
}

/** The verdict line: what the model shows, then every other state that has any condition. */
function leadLine(t: TFn, f: F, lang: string, counts: Record<string, number>, n: number): string {
  const rest = (["declared", "awaiting", "not_met", "not_applicable"] as const)
    .filter((s) => counts[s] > 0)
    .map((s) => t(counts[s] === 1 && s !== "declared" ? `ei.part.${s}.one` : `ei.part.${s}`, { n: f(counts[s]) }));
  const head = { evidence: f(counts.evidence), n: f(n) };
  return rest.length ? t("ei.lead.rest", { ...head, rest: listJoin(lang, rest) }) : t("ei.lead", head);
}

function StateOf({ state }: { state: string }) {
  const t = useT();
  // what the DSO has stated or has still to state, and what does not apply, are not verdicts of the model
  if (state === "declared" || state === "awaiting" || state === "not_applicable") return <span className="status measured">{t(`ei.state.${state}`)}</span>;
  return <Status kind={KIND[state as keyof typeof KIND] ?? "warning"}>{t(`ei.state.${state}`)}</Status>;
}

export function EiSection({ basis, policy }: { basis: string; policy: string }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const q = `basis=${basis}&policy=${policy}`;
  const pack = useApi<any>(`/eipack?${q}`);
  const settings = useApi<any>("/settings");
  const S = settings.data?.settings;
  const [market, setMarket] = useState("");
  const [method, setMethod] = useState("");
  const [review, setReview] = useState("");
  const [saving, setSaving] = useState(false);
  const sealed = useSealedDownload();
  useEffect(() => {
    if (!S) return;
    setMarket(S.market_alternative_statement ?? "");
    setMethod(S.method_approval_reference ?? "");
    setReview(S.review_statement ?? "");
  }, [S]);
  if (!pack.data || !S) return null;
  const P = pack.data;
  const changed = market !== (S.market_alternative_statement ?? "") || method !== (S.method_approval_reference ?? "") || review !== (S.review_statement ?? "");

  const save = async () => {
    setSaving(true);
    const r = await fetch(`${API}/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market_alternative_statement: market, method_approval_reference: method, review_statement: review }),
    }).catch(() => null);
    setSaving(false);
    if (!r) return toast.error(t("ui.offline"));
    if (!r.ok) return toast.error(await responseRefusal(t, r));
    await client.invalidateQueries({ queryKey: ["/settings"] });
    await client.invalidateQueries({ queryKey: [`/eipack?${q}`] });
    toast.info(t("ei.saved"));
  };
  const printMethod = () => {
    const root = document.documentElement;
    root.dataset.print = "ei";
    const done = () => {
      delete root.dataset.print;
      window.removeEventListener("afterprint", done);
    };
    window.addEventListener("afterprint", done);
    window.print();
  };

  return (
    <div className="stack">
      <p className="lead-answer">{leadLine(t, f, lang, P.counts, P.conditions.length)}</p>
      <table className="protocol ei-table">
        <caption className="sr-only">{t("ei.table", { basis: t(`rep.basis.${basis}`) })}</caption>
        <tbody>
          {P.conditions.map((c: any) => (
            <tr key={c.key}>
              <td className="p-mark"><StateOf state={c.state} /></td>
              <th scope="row">{labelOf(c, lang)}</th>
              <td className="p-criterion">{factLine(t, f, c.fact)}<small className="ei-source">{c.source}</small></td>
            </tr>
          ))}
        </tbody>
      </table>
      <section className="block stack" aria-labelledby="ei-statements">
        <div className="block-head"><h3 id="ei-statements">{t("ei.statements")}</h3></div>
        <p className="caption">{t("ei.statements.caption")}</p>
        <div className="field decision-note">
          <label htmlFor="ei-market">{t("ei.market")}</label>
          <textarea id="ei-market" rows={2} maxLength={2000} value={market} onChange={(e) => setMarket(e.target.value)} />
        </div>
        <div className="field decision-note">
          <label htmlFor="ei-method">{t("ei.method")}</label>
          <input id="ei-method" maxLength={200} value={method} onChange={(e) => setMethod(e.target.value)} />
        </div>
        <div className="field decision-note">
          <label htmlFor="ei-review">{t("ei.review")}</label>
          <textarea id="ei-review" rows={2} maxLength={2000} value={review} onChange={(e) => setReview(e.target.value)} />
        </div>
        <div className="answer-actions">
          <button className="btn ghost small" disabled={!changed || saving} onClick={save}>{t("ei.save")}</button>
        </div>
      </section>
      <div>
        <div className="answer-actions">
          <button className="btn" disabled={sealed.busy} onClick={() => sealed.download(`${API}/eipack?${q}&export=true`, `kapacitet-ei-evidence-${basis}.json`)}>
            <Icon name="download" size={15} />{t("ei.export")}
          </button>
          <button className="btn ghost" onClick={printMethod}>{t("ei.print")}</button>
        </div>
        <SealReceipt receipt={sealed.receipt} />
      </div>
      <p className="caption-muted">{t("ei.note", { source: P.source.title })}</p>
      <EiMethodRecord P={P} S={S} />
    </div>
  );
}

/** The method description for Ei, printed on its own (html[data-print="ei"]). */
function EiMethodRecord({ P, S }: { P: any; S: any }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  return createPortal(
    <article className="print-record ei-method" lang={lang} aria-hidden="true">
      <header className="pr-head">
        <p className="pr-brand"><Roundel />Kapacitet+</p>
        <h1>{t("ei.rec.title")}</h1>
        <dl className="pr-meta">
          <dt>{t("rep.basisLabel")}</dt><dd>{t(`rep.basis.${P.basis}`)}</dd>
          <dt>{t("rep.rule")}</dt><dd>{t(`pol.${P.policy}`)}</dd>
          <dt>{t("rec.generated")}</dt><dd>{dateTime(lang, new Date())}</dd>
        </dl>
      </header>
      <section>
        <h2>{t("ei.rec.method")}</h2>
        <p>{t("ei.rec.methodText", { rule: t(`pol.${P.policy}`), level: f(S.agreed_level_pct, 1), years: f(S.deferral_years) })}</p>
      </section>
      <section>
        <h2>{t("ei.rec.conditions")}</h2>
        <table className="pr-table">
          <tbody>
            {P.conditions.map((c: any) => (
              <tr key={c.key}><th scope="row">{labelOf(c, lang)}</th><td>{t(`ei.state.${c.state}`)}</td><td>{factLine(t, f, c.fact)} ({c.source})</td></tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <h2>{t("ei.statements")}</h2>
        <dl className="pr-kv">
          <dt>{t("ei.market")}</dt><dd>{S.market_alternative_statement || t("ei.rec.none")}</dd>
          <dt>{t("ei.method")}</dt><dd>{S.method_approval_reference || t("ei.rec.none")}</dd>
          <dt>{t("ei.review")}</dt><dd>{S.review_statement || t("ei.rec.none")}</dd>
        </dl>
      </section>
      <p className="pr-foot">{t("ei.note", { source: P.source.title })}</p>
    </article>,
    document.body,
  );
}
