import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { toast } from "./Toast";
import { API, apiWrite } from "../lib/api";
import { useCan } from "../lib/auth";
import { RoleNote } from "./RoleNote";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { responseRefusal } from "../lib/errors";
import { fuseText } from "../lib/reasons";
import { useStore } from "../lib/store";

export interface QueueState {
  version: number;
  baseline: "static" | "doe" | null;
  items: any[];
  approved: number;
  updating: boolean;
}

type Decision = "firm" | "nonfirm" | "declined";

/** Why an answer can or cannot be decided on in the queue as it stands. */
function decidable(res: any, q: QueueState): { ok: boolean; why?: string } {
  const asked = res.request?.baseline;
  if (asked === "queue") return res.queue?.version === q.version ? { ok: true } : { ok: false, why: "changed" };
  if (q.approved > 0) return { ok: false, why: "queue" };
  if (q.baseline && q.baseline !== asked) return { ok: false, why: "base" };
  return { ok: true };
}

/**
 * The planner's decision on the answer on screen: connect it firm (only when the static study
 * agrees), connect it with its envelope, or decline it, with a reason kept beside it. Approving
 * re-verifies every weather year and puts the house into the grid the next request meets.
 */
export function DecisionBlock({ res, queue }: { res: any; queue: QueueState }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<Decision | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  // with sign-in on, deciding takes the approver role: anyone else sees the buttons, disabled, and why
  const role = useCan("decide");

  const decided = queue.items.find((it) => it.assessment === res.id);
  if (decided) {
    const when = dateTime(lang, decided.decided);
    return (
      <section className="decision" aria-labelledby="decision-title">
        <h3 id="decision-title">{t("req.decide.title")}</h3>
        <p className="decision-done"><Icon name="check" size={16} />{t(`queue.item.${decided.decision}`)} · {when}</p>
        {decided.actor && (
          <p className="caption-muted">{t("req.decide.byActor", { name: decided.actor.name, role: t(`auth.role.${decided.actor.role}`) })}</p>
        )}
        {decided.note && <p className="caption-muted">{decided.note}</p>}
        {decided.decision !== "declined" && <p className="caption-muted">{t("req.decide.next")}</p>}
      </section>
    );
  }
  const can = decidable(res, queue);
  const firmOk = Object.values(res.static ?? {}).every((v: any) => v?.approved);
  // a limit on what the house exchanges with the grid cannot protect the house's own main fuse: when
  // the fuse cannot carry the request, neither connection is offered and declining comes first
  const fuseShort = res.fuse?.ok === false;
  const running = busy !== null;
  const locked = running || !role.ok;

  const decide = async (decision: Decision) => {
    setError(null);
    setBusy(decision);
    setProgress(decision === "declined" ? "" : t("req.decide.verifying"));
    let r: Response;
    try {
      r = await apiWrite("/queue", { assessment: res.id, decision, note });
    } catch {
      setBusy(null);
      setError(t("req.dropped"));
      return;
    }
    if (!r.ok) {
      setBusy(null);
      setError(await responseRefusal(t, r));
      return;
    }
    const { id } = await r.json();
    const es = new EventSource(`${API}/queue/jobs/${id}/events`);
    esRef.current = es;
    es.addEventListener("state", (e: MessageEvent) => {
      const p = JSON.parse(e.data);
      setProgress(t("req.decide.updating", { done: p.done, of: p.of }));
    });
    const finish = async () => {
      es.close();
      const res2 = await fetch(`${API}/queue/jobs/${id}`);
      setBusy(null);
      setProgress("");
      if (!res2.ok) {
        setError(await responseRefusal(t, res2));
        return;
      }
      await client.invalidateQueries({ queryKey: ["/queue"] });
      toast.info(t(decision === "declined" ? "req.decide.declinedDone" : "req.decide.approvedDone"));
    };
    es.addEventListener("end", finish);
    es.addEventListener("error", () => {
      es.close();
      setBusy(null);
      setError(t("req.dropped"));
    });
  };

  return (
    <section className="decision" aria-labelledby="decision-title" aria-busy={running}>
      <h3 id="decision-title">{t("req.decide.title")}</h3>
      {!can.ok ? (
        <p className="caption-muted">{t(`req.decide.why.${can.why}`)}</p>
      ) : (
        <>
          <p className="caption-muted">{t("req.decide.lead")}</p>
          <div className="field decision-note">
            <label htmlFor="decision-note">{t("req.decide.note")}</label>
            <textarea id="decision-note" rows={2} maxLength={500} value={note} disabled={locked}
              aria-describedby="decision-note-hint" onChange={(e) => setNote(e.target.value)} />
            <p id="decision-note-hint" className="caption-muted">{t("req.decide.noteHint")}</p>
          </div>
          <div className="decision-actions">
            {fuseShort && (
              <button className="btn" disabled={locked} title={role.reason} onClick={() => decide("declined")}>
                {busy === "declined" ? t("req.decide.working") : t("req.decide.declined")}
              </button>
            )}
            <button className={fuseShort ? "btn ghost" : "btn"} disabled={locked || fuseShort} title={role.reason}
              aria-describedby={fuseShort ? "fuse-why" : undefined} onClick={() => decide("nonfirm")}>
              {busy === "nonfirm" ? t("req.decide.working") : t("req.decide.nonfirm")}
            </button>
            <button className="btn ghost" disabled={locked || !firmOk || fuseShort} title={role.reason}
              aria-describedby={fuseShort ? "fuse-why" : firmOk ? undefined : "firm-why"} onClick={() => decide("firm")}>
              {busy === "firm" ? t("req.decide.working") : t("req.decide.firm")}
            </button>
            {!fuseShort && (
              <button className="btn ghost" disabled={locked} title={role.reason} onClick={() => decide("declined")}>
                {busy === "declined" ? t("req.decide.working") : t("req.decide.declined")}
              </button>
            )}
          </div>
          <RoleNote action="decide" />
          {fuseShort && <p className="caption-muted" id="fuse-why">{fuseText(t, f, res.fuse)} {t("req.decide.fuseNo")}</p>}
          {!firmOk && !fuseShort && <p className="caption-muted" id="firm-why">{t("req.decide.firmNo")}</p>}
          {progress && <p className="caption-muted" role="status">{progress}</p>}
        </>
      )}
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
    </section>
  );
}

/** The decisions so far, and the way back to the published grid. */
export function QueueList({ queue }: { queue: QueueState }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const role = useCan("reset_queue");
  if (!queue.items.length) return null;
  const declined = queue.items.length - queue.approved;
  const empty = async () => {
    setConfirm(false);
    const r = await apiWrite("/queue", {}, "DELETE").catch(() => null);
    if (!r?.ok) {
      toast.error(t("queue.emptyFailed"));
      return;
    }
    await client.invalidateQueries({ queryKey: ["/queue"] });
    toast.info(t("queue.emptied"));
  };
  return (
    <details className="queue-list" open>
      <summary>{t("queue.summary", { a: f(queue.approved), d: f(declined) })}</summary>
      <ol>
        {queue.items.map((it) => (
          <li key={it.assessment}>
            <a className="queue-house" href={`#/request?cid=${it.cid}&job=${it.assessment}`} title={t("queue.open")}>
              {it.address || t("dt.unnamed", { id: it.cid })}
            </a>
            <span className={`queue-decision ${it.decision}`}>
              {t(`queue.item.${it.decision}`)}{it.by === "rule" ? ` · ${t("inbox.by.rule")}` : ""}
            </span>
            <span className="queue-when">{dateTime(lang, it.decided)}</span>
            {it.note && <span className="queue-note">{it.note}</span>}
          </li>
        ))}
      </ol>
      <p className="caption-muted">{t("queue.basis", { base: queue.baseline === "doe" ? t("req.base.doe") : t("req.base.static") })}</p>
      <p className="caption-muted">{t("queue.sealed")}</p>
      <div className="row">
        <a className="btn ghost small" href={`${API}/queue/log.csv`} download>
          <Icon name="download" size={15} />{t("queue.log")}
        </a>
        <button className="btn ghost small" disabled={queue.updating || !role.ok} title={role.reason} onClick={() => setConfirm(true)}>{t("queue.empty")}</button>
      </div>
      <RoleNote action="reset_queue" />
      <Dialog open={confirm} onClose={() => setConfirm(false)} labelledBy="queue-confirm-title" width={440}>
        <div className="sheet">
          <h2 id="queue-confirm-title">{t("queue.confirm.title")}</h2>
          <p>{t("queue.confirm.body")}</p>
          <div className="decision-actions">
            <button className="btn ghost" onClick={() => setConfirm(false)}>{t("ui.cancel")}</button>
            <button className="btn ghost danger" onClick={empty}>{t("queue.empty")}</button>
          </div>
        </div>
      </Dialog>
    </details>
  );
}
