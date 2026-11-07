/**
 * Applications: a request to connect, with the DSO's rule's proposal, decided by a person unless the
 * DSO switched the rule on and a person signed it for that kind of request (api/inbox.py; advisor
 * mode is the default). The rule connects firm or with the verified envelope; it never declines.
 *
 * `ApplyBlock` sits under an answer on the request screen and turns it into an application;
 * `InboxSection` is the DSO's view of all of them on the operation screen, with what the inbox has
 * done measured from its own events, and `RuleBlock` says what the rule may decide and lets a person
 * sign it (PUT /settings/rule-signature).
 */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, type Col } from "./DataTable";
import { Dialog } from "./Dialog";
import { DecisionBlock, type QueueState } from "./Queue";
import { Icon } from "./Icon";
import { Bar } from "./Skeleton";
import { Status, Tile } from "./bits";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { useCan, useMe } from "../lib/auth";
import { RoleNote } from "./RoleNote";
import { responseRefusal } from "../lib/errors";
import { dateTime, duration, listJoin, studyDate, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { describeRequest, reasonLine } from "../lib/reasons";
import { useStore } from "../lib/store";

export interface Application {
  id: string;
  /** the first four characters of the reference code: the code itself is shown once, to whoever applied */
  reference_hint: string | null;
  received: string;
  cid: number;
  address: string | null;
  source: string;
  request: any;
  assessment: string;
  assessments: string[];
  status: "pending" | "review" | "firm" | "nonfirm" | "declined";
  reasons: { code: string; params: Record<string, number | string> }[];
  /** what the rule would decide, kept when a person has to (advisor mode) */
  proposal?: "firm" | "nonfirm" | "review" | null;
  proposal_reasons?: { code: string; params: Record<string, number | string> }[];
  decided: string | null;
  by: "rule" | "dso" | null;
  pv_kw: number;
  evaluations: number;
}

/** An application named by its hint, never by its code (api/inbox.py keeps only the code's hash). */
export function refHint(app: Pick<Application, "reference_hint">): string {
  return app.reference_hint ? `KP-${app.reference_hint}-…` : "KP-…";
}

type Thresholds = { curtailment_mean_pct: number; curtailment_p90_pct: number; ev_unserved_pct: number };

const KINDS = ["pv", "ev", "hp", "battery"] as const;
type Kind = (typeof KINDS)[number];

interface Signature {
  signed_by: string;
  role: string;
  signed_at: string;
  request_kinds: Kind[];
  max_pv_kwp: number;
  thresholds: Thresholds;
  valid_until: string;
  /** with sign-in on: the person signed in who signed (`signed_by` is their name) */
  actor?: { sub: string; name: string; role: string } | null;
}

export interface Inbox {
  items: Application[];
  working: string | null;
  auto_decisions: boolean;
  thresholds: Thresholds;
  rule_signature: Signature | null;
  /** whether the signature can decide anything today (api/inbox.py signature_state) */
  signature_state: "none" | "expired" | "thresholds_changed" | "in_force";
}

const STATUS_KIND = { firm: "good", nonfirm: "good", review: "warning", declined: "critical" } as const;

/** The inbox, polled while anything in it is still being decided. */
function useInbox() {
  const first = useApi<Inbox>("/inbox");
  const busy = !!first.data && (first.data.working !== null || first.data.items.some((a) => a.status === "pending"));
  return useApi<Inbox>("/inbox", { refetchMs: busy ? 1500 : undefined });
}

function StatusOf({ app }: { app: Application }) {
  const t = useT();
  if (app.status === "pending") return <span className="status measured">{t("inbox.status.pending")}</span>;
  return <Status kind={STATUS_KIND[app.status]}>{t(`inbox.status.${app.status}`)}</Status>;
}

/** Under an answer: apply for the connection it describes, or where that application stands. */
export function ApplyBlock({ res, queue }: { res: any; queue: QueueState }) {
  const t = useT();
  const client = useQueryClient();
  const inbox = useInbox();
  const role = useCan("applications");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // the code the server gave when this page applied: it is never sent again, so it is kept for the visit
  const [code, setCode] = useState<string | null>(null);
  const app = inbox.data?.items.find((a) => a.assessments.includes(res.id));
  // a decision taken directly on this answer leaves nothing to apply for
  const decided = queue.items.some((it) => it.assessment === res.id);
  const was = useRef(app?.status);
  useEffect(() => {
    if (was.current === "pending" && app && app.status !== "pending") {
      void client.invalidateQueries({ queryKey: ["/queue"] });
      toast.info(t(`inbox.done.${app.status}`, { ref: code ?? refHint(app) }));
    }
    was.current = app?.status;
  }, [app?.status, app, client, t, code]);
  if (decided && !app) return null;

  const apply = async () => {
    setBusy(true);
    setError(null);
    const r = await apiWrite("/applications", { assessment: res.id }).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    const body = await r.json();
    setCode(body.reference ?? null);
    await client.invalidateQueries({ queryKey: ["/inbox"] });
    toast.info(t("inbox.applied", { ref: body.reference }));
  };

  return (
    <section className="decision" aria-labelledby="apply-title">
      <h3 id="apply-title">{t("inbox.apply.title")}</h3>
      {app ? (
        <p className="apply-state" role="status">
          <StatusOf app={app} /> <span>{code ?? refHint(app)}</span>
          <a href="#/operation">{t("inbox.apply.open")}</a>
        </p>
      ) : (
        <>
          <p className="caption-muted">{t("inbox.apply.lead")}</p>
          <div className="decision-actions">
            <button className="btn" disabled={busy || !role.ok} title={role.reason} onClick={apply}>{busy ? t("req.decide.working") : t("inbox.apply.go")}</button>
          </div>
          <RoleNote action="applications" />
        </>
      )}
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
    </section>
  );
}

/** The DSO's inbox on the operation screen. */
export function InboxSection() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const inbox = useInbox();
  const canDecide = useCan("decide");
  const canRequeue = useCan("applications");
  const kpi = useApi<any>("/inbox/kpis", { refetchMs: inbox.data?.items.some((a) => a.status === "pending") ? 1500 : undefined });
  const queue = useApi<QueueState>("/queue");
  const [open, setOpen] = useState<Application | null>(null);

  // when the worker settles an application, the queue it wrote to is read again
  const settled = inbox.data?.items.filter((a) => a.status !== "pending").length ?? 0;
  useEffect(() => {
    void client.invalidateQueries({ queryKey: ["/queue"] });
    void client.invalidateQueries({ queryKey: ["/inbox/kpis"] });
  }, [settled, client]);

  if (inbox.isLoading || kpi.isLoading) return <Lines n={4} />;
  if (!inbox.data || !kpi.data) return null;
  const I = inbox.data;
  const K = kpi.data;

  const requeue = async (app: Application) => {
    const r = await apiWrite(`/applications/${app.id}/requeue`).catch(() => null);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/inbox"] });
  };

  const waitOf = (a: Application) => a.decided ? (Date.parse(a.decided) - Date.parse(a.received)) / 1000 : null;
  const cols: Col<Application>[] = [
    { key: "ref", label: t("inbox.col.ref"), value: (a) => refHint(a) },
    { key: "house", label: t("inbox.col.house"), value: (a) => a.address ?? "",
      cell: (a) => <a href={`#/request?cid=${a.cid}&job=${a.assessment}`}>{a.address || t("dt.unnamed", { id: a.cid })}</a> },
    { key: "asked", label: t("inbox.col.asked"), value: (a) => describeRequest(t, f, a.request) },
    { key: "status", label: t("inbox.col.status"), value: (a) => t(`inbox.status.${a.status}`),
      cell: (a) => (
        <span className="inbox-status">
          <StatusOf app={a} />
          {a.by && <small>{t(`inbox.by.${a.by}`)}</small>}
          {a.status === "review" && a.reasons.map((r, i) => <small key={i} className="inbox-reason">{reasonLine(t, f, r)}</small>)}
          {a.status === "review" && a.proposal && <small className="inbox-proposal">{t(`inbox.proposal.${a.proposal}`)}</small>}
        </span>
      ) },
    { key: "received", label: t("inbox.col.received"), value: (a) => a.received, cell: (a) => dateTime(lang, a.received) },
    { key: "wait", label: t("inbox.col.wait"), value: (a) => waitOf(a), cell: (a) => duration(lang, waitOf(a)), numeric: true },
    { key: "act", label: t("inbox.col.act"), value: () => "",
      cell: (a) => a.status === "review" ? (
        <span className="inbox-actions">
          {/* row actions stay secondary: the primary belongs to the decision dialog they open */}
          <button className="btn ghost small" disabled={!canDecide.ok} title={canDecide.reason} onClick={() => setOpen(a)}>{t("inbox.decide")}</button>
          <button className="btn ghost small" disabled={!canRequeue.ok} title={canRequeue.reason} onClick={() => void requeue(a)}>{t("inbox.again")}</button>
        </span>
      ) : null },
  ];

  return (
    <div className="stack">
      <div className="tiles cols-4">
        <Tile label={t("inbox.kpi.received")} value={f(K.received)}
          note={t("inbox.kpi.receivedNote", { pending: f(K.pending), review: f(K.review) })} />
        <Tile label={t("inbox.kpi.byRule")} value={K.share_by_rule_pct === null ? "–" : f(K.share_by_rule_pct)} unit={K.share_by_rule_pct === null ? undefined : "%"}
          note={t("inbox.kpi.byRuleNote", { n: f(K.decided_by_rule), of: f(K.decided) })} />
        <Tile label={t("inbox.kpi.wait")} value={duration(lang, K.median_s)}
          note={t("inbox.kpi.waitNote", { p90: duration(lang, K.p90_s) })} />
        <Tile label={t("inbox.kpi.connected")} value={f(K.firm_kw + K.nonfirm_kw, 1)} unit="kW"
          note={t("inbox.kpi.connectedNote", { firm: f(K.firm_kw, 1), nonfirm: f(K.nonfirm_kw, 1) })} />
      </div>
      <RuleBlock inbox={I} />
      {I.items.length === 0 ? (
        <p className="caption">{t("inbox.empty")} <a href="#/request">{t("inbox.emptyGo")}</a></p>
      ) : (
        <DataTable rows={I.items} cols={cols} rowKey={(a) => a.id} caption={t("inbox.table")} searchLabel={t("inbox.search")} className="inbox-dt" />
      )}
      <p className="caption-muted">{t("inbox.note")} <a href="#/apply" target="_blank" rel="noopener">{t("inbox.portal")}</a></p>
      <RoleNote action="decide" />
      <Dialog open={open !== null} onClose={() => setOpen(null)} labelledBy="inbox-dialog-title" width={560}>
        {open && queue.data && (
          <div className="sheet">
            <h2 id="inbox-dialog-title">{t("inbox.dialog", { ref: refHint(open) })}</h2>
            <DecideOne app={open} queue={queue.data} />
          </div>
        )}
      </Dialog>
    </div>
  );
}

/** One application in review, decided with the queue's own decision. */
function DecideOne({ app, queue }: { app: Application; queue: QueueState }) {
  const t = useT();
  const f = useFmt();
  const res = useApi<any>(`/requests/${app.assessment}`);
  return (
    <div className="stack">
      <p className="caption">{app.address} · {describeRequest(t, f, app.request)}</p>
      <ul className="inbox-reasons">{app.reasons.map((r, i) => <li key={i}>{reasonLine(t, f, r)}</li>)}</ul>
      {app.proposal && (
        <p className="caption inbox-proposal-line">
          {t(`inbox.proposal.${app.proposal}`)}
          {(app.proposal_reasons ?? []).map((r, i) => <small key={i} className="inbox-reason">{reasonLine(t, f, r)}</small>)}
        </p>
      )}
      <p><a href={`#/request?cid=${app.cid}&job=${app.assessment}`}>{t("inbox.openAnswer")}</a></p>
      {res.data ? <DecisionBlock res={res.data} queue={queue} /> : <Lines n={3} />}
    </div>
  );
}

/** What the DSO's rule may decide on its own: advisor mode unless it is switched on and a person's
 * signature covers today's thresholds, and the form to sign it. */
export function RuleBlock({ inbox: I }: { inbox: Inbox }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [signing, setSigning] = useState(false);
  const canSet = useCan("settings");
  const canSign = useCan("sign_rule");
  const sig = I.rule_signature ?? null;
  const advisor = !I.auto_decisions || I.signature_state !== "in_force";
  const th = I.thresholds;

  const setAuto = async (on: boolean) => {
    setSaving(true);
    const r = await apiWrite("/settings", { auto_decisions: on }, "PUT").catch(() => null);
    setSaving(false);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/inbox"] });
    await client.invalidateQueries({ queryKey: ["/settings"] });
    toast.info(t(on ? "inbox.auto.onDone" : "inbox.auto.offDone"));
  };

  const kinds = (ks: Kind[]) => listJoin(lang, ks.map((k) => t(`inbox.kind.${k}`)));
  const who = sig ? { by: sig.signed_by, role: sig.role } : { by: "", role: "" };
  return (
    <section className="block stack" aria-labelledby="inbox-rule">
      <div className="block-head">
        <h3 id="inbox-rule">{t("inbox.rule.title")}</h3>
        <span className="status measured">{t(advisor ? "inbox.mode.advisorTag" : "inbox.mode.autoTag")}</span>
      </div>
      <p className="rule-mode">
        <strong>{advisor ? t("inbox.mode.advisor") : t("inbox.mode.auto", who)}</strong>{" "}
        {sig === null && t("inbox.sig.none")}
        {sig !== null && I.signature_state === "in_force" && t("inbox.sig.inForce", {
          ...who, at: dateTime(lang, sig.signed_at, false), kinds: kinds(sig.request_kinds),
          kwp: f(sig.max_pv_kwp, sig.max_pv_kwp % 1 ? 1 : 0), until: studyDate(lang, sig.valid_until) })}
        {sig !== null && I.signature_state === "expired" && t("inbox.sig.expired", { ...who, until: studyDate(lang, sig.valid_until) })}
        {sig !== null && I.signature_state === "thresholds_changed" && t("inbox.sig.changed", who)}
      </p>
      <p className="caption-muted">{t("inbox.auto.rule", { mean: f(th.curtailment_mean_pct, 1), p90: f(th.curtailment_p90_pct, 1), ev: f(th.ev_unserved_pct, 1) })}</p>
      <div className="report-controls">
        <div className="seg" role="group" aria-label={t("inbox.auto.label")}>
          <button aria-pressed={!I.auto_decisions} disabled={saving || !canSet.ok} title={canSet.reason} onClick={() => I.auto_decisions && void setAuto(false)}>{t("inbox.auto.off")}</button>
          <button aria-pressed={I.auto_decisions} disabled={saving || !canSet.ok} title={canSet.reason} onClick={() => !I.auto_decisions && void setAuto(true)}>{t("inbox.auto.on")}</button>
        </div>
        <button className="btn ghost small" disabled={!canSign.ok} title={canSign.reason} onClick={() => setSigning(true)}>{t(sig ? "inbox.sign.again" : "inbox.sign.open")}</button>
      </div>
      <RoleNote action="settings" />
      <Dialog open={signing} onClose={() => setSigning(false)} labelledBy="inbox-sign-title" width={520}>
        {signing && <SignRule thresholds={th} last={sig} onDone={() => setSigning(false)} />}
      </Dialog>
    </section>
  );
}

/** The signature form: who signs and in what role, for which requests, up to what size and until when.
 * It signs the thresholds in force; the server dates it and seals it. */
function SignRule({ thresholds: th, last, onDone }: { thresholds: Thresholds; last: Signature | null; onDone: () => void }) {
  const t = useT();
  const f = useFmt();
  const client = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  // with sign-in on, the server signs in the session's name, whatever the field says: so it shows that
  const me = useMe().data;
  const session = me?.enabled && me.user ? me.user.name : null;
  const [typed, setBy] = useState(last?.signed_by ?? "");
  const by = session ?? typed;
  const [role, setRole] = useState(last?.role ?? "");
  const [kinds, setKinds] = useState<Kind[]>(last?.request_kinds ?? ["pv"]);
  const [kwp, setKwp] = useState(last ? String(last.max_pv_kwp) : "");
  const [until, setUntil] = useState(last && last.valid_until >= today ? last.valid_until : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const size = Number(kwp);
  const ready = by.trim() !== "" && role.trim() !== "" && kinds.length > 0 && kwp.trim() !== "" &&
    Number.isFinite(size) && size >= 0 && size <= 1000 && until > today;

  const toggle = (k: Kind, on: boolean) =>
    setKinds((ks) => (on ? KINDS.filter((x) => x === k || ks.includes(x)) : ks.filter((x) => x !== k)));
  const sign = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    const r = await apiWrite("/settings/rule-signature",
      { signed_by: by.trim(), role: role.trim(), request_kinds: kinds, max_pv_kwp: size, valid_until: until }, "PUT").catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError((await responseRefusal(t, r)) || t("inbox.sign.invalid"));
    await client.invalidateQueries({ queryKey: ["/inbox"] });
    await client.invalidateQueries({ queryKey: ["/settings"] });
    toast.info(t("inbox.sign.done", { by: by.trim() }));
    onDone();
  };

  return (
    <form className="sheet stack" onSubmit={sign}>
      <h2 id="inbox-sign-title">{t("inbox.sign.title")}</h2>
      <p className="caption">{t("inbox.sign.lead", { mean: f(th.curtailment_mean_pct, 1), p90: f(th.curtailment_p90_pct, 1), ev: f(th.ev_unserved_pct, 1) })}</p>
      <div className="field decision-note">
        <label htmlFor="sign-by">{t("inbox.sign.by")}</label>
        <input id="sign-by" maxLength={120} required autoComplete="name" value={by} readOnly={session !== null} onChange={(e) => setBy(e.target.value)} />
      </div>
      <div className="field decision-note">
        <label htmlFor="sign-role">{t("inbox.sign.role")}</label>
        <input id="sign-role" maxLength={120} required autoComplete="organization-title" value={role} onChange={(e) => setRole(e.target.value)} />
      </div>
      <fieldset className="sign-kinds">
        <legend>{t("inbox.sign.kinds")}</legend>
        {KINDS.map((k) => (
          <label key={k} className="field-inline">
            <input type="checkbox" checked={kinds.includes(k)} onChange={(e) => toggle(k, e.target.checked)} />
            <span>{t(`inbox.kind.${k}`)}</span>
          </label>
        ))}
      </fieldset>
      {/* the size and the last day as the same labelled fields as the name and the role */}
      <div className="sign-pair">
        <div className="field decision-note">
          <label htmlFor="sign-kwp">{t("inbox.sign.kwp")} (kWp)</label>
          <input id="sign-kwp" type="number" inputMode="decimal" min={0} max={1000} step={0.5} required value={kwp} onChange={(e) => setKwp(e.target.value)} />
        </div>
        <div className="field decision-note">
          <label htmlFor="sign-until">{t("inbox.sign.until")}</label>
          <input id="sign-until" type="date" min={new Date(Date.parse(today) + 864e5).toISOString().slice(0, 10)} required value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
      </div>
      <p className="caption-muted">{t("inbox.sign.note")}</p>
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
      <div className="decision-actions">
        <button type="submit" className="btn" disabled={!ready || busy}>{busy ? t("req.decide.working") : t("inbox.sign.go")}</button>
        <button type="button" className="btn ghost" onClick={onDone}>{t("inbox.sign.cancel")}</button>
      </div>
    </form>
  );
}

function Lines({ n }: { n: number }) {
  return <div className="stack" aria-hidden="true">{Array.from({ length: n }, (_, i) => <Bar key={i} w={`${100 - i * 12}%`} />)}</div>;
}
