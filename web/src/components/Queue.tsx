import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { toast } from "./Toast";
import { API } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
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
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<Decision | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const decided = queue.items.find((it) => it.assessment === res.id);
  if (decided) {
    const when = new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "en-GB", { dateStyle: "medium", timeStyle: "short" })
      .format(new Date(decided.decided));
    return (
      <section className="decision" aria-labelledby="decision-title">
        <h3 id="decision-title">{t("req.decide.title")}</h3>
        <p className="decision-done"><Icon name="check" size={16} />{t(`queue.item.${decided.decision}`)} · {when}</p>
        {decided.note && <p className="caption-muted">{decided.note}</p>}
        {decided.decision !== "declined" && <p className="caption-muted">{t("req.decide.next")}</p>}
      </section>
    );
  }
  const can = decidable(res, queue);
  const firmOk = Object.values(res.static ?? {}).every((v: any) => v?.approved);
  const running = busy !== null;

  const decide = async (decision: Decision) => {
    setError(null);
    setBusy(decision);
    setProgress(decision === "declined" ? "" : t("req.decide.verifying"));
    let r: Response;
    try {
      r = await fetch(`${API}/queue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assessment: res.id, decision, note }),
      });
    } catch {
      setBusy(null);
      setError(t("req.dropped"));
      return;
    }
    if (!r.ok) {
      setBusy(null);
      setError(await r.json().then((b) => b.detail).catch(() => `${r.status}`));
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
        setError(await res2.json().then((b) => b.detail).catch(() => `${res2.status}`));
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
            <textarea id="decision-note" rows={2} maxLength={500} value={note} disabled={running}
              onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="decision-actions">
            <button className="btn" disabled={running} onClick={() => decide("nonfirm")}>
              {busy === "nonfirm" ? t("req.decide.working") : t("req.decide.nonfirm")}
            </button>
            <button className="btn ghost" disabled={running || !firmOk} aria-describedby={firmOk ? undefined : "firm-why"}
              onClick={() => decide("firm")}>
              {busy === "firm" ? t("req.decide.working") : t("req.decide.firm")}
            </button>
            <button className="btn ghost" disabled={running} onClick={() => decide("declined")}>
              {busy === "declined" ? t("req.decide.working") : t("req.decide.declined")}
            </button>
          </div>
          {!firmOk && <p className="caption-muted" id="firm-why">{t("req.decide.firmNo")}</p>}
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
  if (!queue.items.length) return null;
  const declined = queue.items.length - queue.approved;
  const when = new Intl.DateTimeFormat(lang === "es" ? "es-ES" : "en-GB", { dateStyle: "short", timeStyle: "short" });
  const empty = async () => {
    setConfirm(false);
    const r = await fetch(`${API}/queue`, { method: "DELETE" }).catch(() => null);
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
            <span className="queue-house">{it.address || t("dt.unnamed", { id: it.cid })}</span>
            <span className={`queue-decision ${it.decision}`}>{t(`queue.item.${it.decision}`)}</span>
            <span className="queue-when">{when.format(new Date(it.decided))}</span>
            {it.note && <span className="queue-note">{it.note}</span>}
          </li>
        ))}
      </ol>
      <p className="caption-muted">{t("queue.basis", { base: queue.baseline === "doe" ? t("req.base.doe") : t("req.base.static") })}</p>
      <button className="btn ghost small" disabled={queue.updating} onClick={() => setConfirm(true)}>{t("queue.empty")}</button>
      <Dialog open={confirm} onClose={() => setConfirm(false)} labelledBy="queue-confirm-title" width={440}>
        <div className="sheet">
          <h2 id="queue-confirm-title">{t("queue.confirm.title")}</h2>
          <p>{t("queue.confirm.body")}</p>
          <div className="decision-actions">
            <button className="btn" onClick={empty}>{t("queue.empty")}</button>
            <button className="btn ghost" onClick={() => setConfirm(false)}>{t("ui.cancel")}</button>
          </div>
        </div>
      </Dialog>
    </details>
  );
}
