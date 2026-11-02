/**
 * Imported networks (api/datasets.py): create one from an export that passed the check, follow its
 * build, and switch the server to it, or back. A switch restarts the server on the network's own
 * folder, so it always asks first, and it never happens by itself.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { Status } from "./bits";
import { toast } from "./Toast";
import { API, useApi } from "../lib/api";
import { responseRefusal } from "../lib/errors";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

interface Net {
  id: string;
  name: string;
  created: string;
  status: "uploaded" | "building" | "ready" | "failed";
  customers?: number;
  quick?: boolean;
  error?: string;
  building?: boolean;
  steps: { name: string; status: string; error?: string }[];
}

interface Listing {
  current: string;
  active: string;
  supervised: boolean;
  reference: { id: string; name: string };
  items: Net[];
}

/** After an export passes the check: keep it as a network and start building it. */
export function CreateNetwork({ files }: { files: Record<string, string> }) {
  const t = useT();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [quick, setQuick] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    setBusy(true);
    setError(null);
    const r = await fetch(`${API}/datasets`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() || t("net.create.default"), files, quick }),
    }).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    const body = await r.json();
    await client.invalidateQueries({ queryKey: ["/datasets"] });
    toast.info(t("net.created", { name: body.name }));
  };
  return (
    <div className="stack netcreate">
      <h4>{t("net.create.title")}</h4>
      <div className="report-controls">
        <label className="field-inline">
          <span>{t("net.create.name")}</span>
          <input value={name} maxLength={80} placeholder={t("net.create.default")} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field-inline">
          <input type="checkbox" checked={quick} onChange={(e) => setQuick(e.target.checked)} />
          <span>{t("net.create.quick")}</span>
        </label>
        <button className="btn small" disabled={busy} onClick={create}>{t("net.create.go")}</button>
      </div>
      <p className="caption-muted">{t(quick ? "net.create.quickNote" : "net.create.fullNote")}</p>
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
    </div>
  );
}

/** Every network, the one in use, and the way to switch. */
export function NetworksPanel() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const first = useApi<Listing>("/datasets");
  const anyBuilding = !!first.data?.items.some((n) => n.status === "building" || n.building);
  const list = useApi<Listing>("/datasets", { refetchMs: anyBuilding ? 3000 : undefined });
  const [target, setTarget] = useState<{ id: string; name: string } | null>(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // the server comes back on the other network: the page reloads once /health says so
  useEffect(() => {
    if (!switching || !target) return;
    const id = window.setInterval(async () => {
      const r = await fetch(`${API}/health`).catch(() => null);
      const h = r?.ok ? await r.json().catch(() => null) : null;
      if (h?.dataset === target.id) window.location.reload();
    }, 1000);
    return () => window.clearInterval(id);
  }, [switching, target]);

  const L = list.data;
  if (!L) return null;

  const activate = async () => {
    if (!target) return;
    setError(null);
    const r = await fetch(`${API}/datasets/${target.id}/activate`, { method: "POST" }).catch(() => null);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    setSwitching(true);
  };
  const rebuild = async (n: Net) => {
    const r = await fetch(`${API}/datasets/${n.id}/build`, { method: "POST" }).catch(() => null);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/datasets"] });
  };
  const remove = async (n: Net) => {
    const r = await fetch(`${API}/datasets/${n.id}`, { method: "DELETE" }).catch(() => null);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/datasets"] });
    toast.info(t("net.deleted", { name: n.name }));
  };
  const rows: (Net & { reference?: boolean })[] = [
    { id: L.reference.id, name: L.reference.name, created: "", status: "ready", steps: [], reference: true },
    ...L.items,
  ];

  return (
    <section className="block stack" aria-labelledby="nets-title">
      <div className="block-head"><h3 id="nets-title">{t("net.title")}</h3></div>
      <p className="caption">{t("net.caption")}</p>
      <ul className="nets">
        {rows.map((n) => {
          const done = n.steps.filter((s) => s.status === "done").length;
          const running = n.steps.find((s) => s.status === "running");
          const failed = n.steps.find((s) => s.status === "failed");
          const current = n.id === L.current;
          return (
            <li key={n.id} className={current ? "current" : undefined}>
              <div className="nets-name">
                <strong>{n.name}</strong>
                <small>{n.reference ? t("net.reference") : t("net.imported", { n: f(n.customers ?? 0), when: dateTime(lang, n.created, false) })}</small>
              </div>
              <div className="nets-state">
                {current ? <Status kind="good">{t("net.inUse")}</Status>
                  : n.status === "ready" ? <span className="status measured">{t("net.status.ready")}</span>
                  : n.status === "failed" ? <Status kind="critical">{t("net.status.failed")}</Status>
                  : <span className="status measured">{t(`net.status.${n.status}`)}</span>}
                {n.status === "building" && (
                  <small role="status">{t("net.steps", { done: f(done), of: f(n.steps.length), step: running ? t(`net.step.${running.name}`) : "" })}</small>
                )}
                {failed && <small>{t("net.failedAt", { step: t(`net.step.${failed.name}`), error: failed.error ?? "" })}</small>}
              </div>
              <div className="nets-actions">
                {!current && n.status === "ready" && L.supervised && (
                  <button className="btn small" disabled={switching} onClick={() => setTarget({ id: n.id, name: n.name })}>{t("net.use")}</button>
                )}
                {!n.reference && n.status === "failed" && (
                  <button className="btn ghost small" disabled={switching} onClick={() => void rebuild(n)}>{t("net.rebuild")}</button>
                )}
                {!n.reference && !current && n.status !== "building" && (
                  <button className="btn ghost small" disabled={switching} onClick={() => void remove(n)}>{t("net.delete")}</button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {!L.supervised && <p className="caption-muted">{t("net.unsupervised")}</p>}
      {L.current !== L.reference.id && <p className="caption-muted">{t("net.shared")}</p>}
      <Dialog open={target !== null} onClose={() => { if (!switching) setTarget(null); }} labelledBy="net-switch-title" width={460}>
        {target && (
          <div className="sheet">
            <h2 id="net-switch-title">{t("net.switch.title", { name: target.name })}</h2>
            <p>{t("net.switch.body")}</p>
            {switching ? <p role="status">{t("net.switching")}</p> : (
              <div className="decision-actions">
                <button className="btn ghost" onClick={() => setTarget(null)}>{t("ui.cancel")}</button>
                <button className="btn" onClick={activate}>{t("net.switch.go")}</button>
              </div>
            )}
            {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
          </div>
        )}
      </Dialog>
    </section>
  );
}
