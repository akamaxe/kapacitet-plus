/**
 * Applications: a request to connect, decided by the DSO's rule when it can be and by a person when
 * it cannot (api/inbox.py). The rule connects firm or with the verified envelope; it never declines.
 *
 * `ApplyBlock` sits under an answer on the request screen and turns it into an application;
 * `InboxSection` is the DSO's view of all of them on the operation screen, with what the inbox has
 * done measured from its own events.
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DataTable, type Col } from "./DataTable";
import { Dialog } from "./Dialog";
import { DecisionBlock, type QueueState } from "./Queue";
import { Icon } from "./Icon";
import { Bar } from "./Skeleton";
import { Status, Tile } from "./bits";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { responseRefusal } from "../lib/errors";
import { dateTime, duration, useFmt } from "../lib/format";
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
  decided: string | null;
  by: "rule" | "dso" | null;
  pv_kw: number;
  evaluations: number;
}

/** An application named by its hint, never by its code (api/inbox.py keeps only the code's hash). */
export function refHint(app: Pick<Application, "reference_hint">): string {
  return app.reference_hint ? `KP-${app.reference_hint}-…` : "KP-…";
}

interface Inbox {
  items: Application[];
  working: string | null;
  auto_decisions: boolean;
  thresholds: { curtailment_mean_pct: number; curtailment_p90_pct: number; ev_unserved_pct: number };
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
            <button className="btn" disabled={busy} onClick={apply}>{busy ? t("req.decide.working") : t("inbox.apply.go")}</button>
          </div>
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
  const kpi = useApi<any>("/inbox/kpis", { refetchMs: inbox.data?.items.some((a) => a.status === "pending") ? 1500 : undefined });
  const queue = useApi<QueueState>("/queue");
  const [open, setOpen] = useState<Application | null>(null);
  const [saving, setSaving] = useState(false);

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

  const setAuto = async (on: boolean) => {
    setSaving(true);
    const r = await apiWrite("/settings", { auto_decisions: on }, "PUT").catch(() => null);
    setSaving(false);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/inbox"] });
    await client.invalidateQueries({ queryKey: ["/settings"] });
    toast.info(t(on ? "inbox.auto.onDone" : "inbox.auto.offDone"));
  };
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
        </span>
      ) },
    { key: "received", label: t("inbox.col.received"), value: (a) => a.received, cell: (a) => dateTime(lang, a.received) },
    { key: "wait", label: t("inbox.col.wait"), value: (a) => waitOf(a), cell: (a) => duration(lang, waitOf(a)), numeric: true },
    { key: "act", label: t("inbox.col.act"), value: () => "",
      cell: (a) => a.status === "review" ? (
        <span className="inbox-actions">
          {/* row actions stay secondary: the primary belongs to the decision dialog they open */}
          <button className="btn ghost small" onClick={() => setOpen(a)}>{t("inbox.decide")}</button>
          <button className="btn ghost small" onClick={() => void requeue(a)}>{t("inbox.again")}</button>
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
      <div className="report-controls">
        <div className="seg" role="group" aria-label={t("inbox.auto.label")}>
          <button aria-pressed={I.auto_decisions} disabled={saving} onClick={() => !I.auto_decisions && void setAuto(true)}>{t("inbox.auto.on")}</button>
          <button aria-pressed={!I.auto_decisions} disabled={saving} onClick={() => I.auto_decisions && void setAuto(false)}>{t("inbox.auto.off")}</button>
        </div>
        <p className="caption-muted">
          {I.auto_decisions
            ? t("inbox.auto.rule", { mean: f(I.thresholds.curtailment_mean_pct, 1), p90: f(I.thresholds.curtailment_p90_pct, 1), ev: f(I.thresholds.ev_unserved_pct, 1) })
            : t("inbox.auto.offNote")}
        </p>
      </div>
      {I.items.length === 0 ? (
        <p className="caption">{t("inbox.empty")} <a href="#/request">{t("inbox.emptyGo")}</a></p>
      ) : (
        <DataTable rows={I.items} cols={cols} rowKey={(a) => a.id} caption={t("inbox.table")} searchLabel={t("inbox.search")} className="inbox-dt" />
      )}
      <p className="caption-muted">{t("inbox.note")} <a href="#/apply" target="_blank" rel="noopener">{t("inbox.portal")}</a></p>
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
      <p><a href={`#/request?cid=${app.cid}&job=${app.assessment}`}>{t("inbox.openAnswer")}</a></p>
      {res.data ? <DecisionBlock res={res.data} queue={queue} /> : <Lines n={3} />}
    </div>
  );
}

function Lines({ n }: { n: number }) {
  return <div className="stack" aria-hidden="true">{Array.from({ length: n }, (_, i) => <Bar key={i} w={`${100 - i * 12}%`} />)}</div>;
}
