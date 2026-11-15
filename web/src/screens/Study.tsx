/**
 * A study of many stations, in the workbench (task 2.1 of the technical review of 14-11-2026): its
 * stations with their state, the capacity each can take for generation and consumption, the limit
 * that binds and the margin; the build with its progress; and a run taken through its steps, collect,
 * review, sign, publish and compare, with the files each writes to download. Everything is the study
 * module's own work (api/studies.py), so the command line and this screen write the same files.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Status } from "../components/bits";
import { DataTable, type Col } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { RoleNote } from "../components/RoleNote";
import { toast } from "../components/Toast";
import { PageSkeleton } from "../components/Skeleton";
import { API, apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { log } from "../lib/log";
import { RUN_STEPS, runChoices, stepDone, stepReady, type RunStep, type StationRow, type StudyDetail } from "../lib/studies";
import { useStore } from "../lib/store";

const STATE_KIND: Record<string, "good" | "warning" | "critical" | undefined> = { ready: "good", failed: "critical" };

export default function Study({ id }: { id: string }) {
  const t = useT();
  const f = useFmt();
  const qc = useQueryClient();
  const can = useCan("studies");
  const canSign = useCan("sign_study");
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [signer, setSigner] = useState("");
  const [findings, setFindings] = useState<string[]>([]);
  const [building, setBuilding] = useState(false);
  const detail = useApi<StudyDetail>(`/studies/${id}`, { refetchMs: building ? 2000 : undefined });
  const d = detail.data;
  const isBuilding = !!d?.study.building;
  if (isBuilding !== building) setBuilding(isBuilding);
  const run = runId ?? d?.runs[0]?.run ?? d?.current_run ?? null;
  const files = useApi<{ files: { path: string; bytes: number }[] }>(
    run && d?.runs.some((r) => r.run === run) ? `/studies/${id}/runs/${run}/files` : null);
  const state = d?.runs.find((r) => r.run === run);
  const ready = d ? d.stations.filter((s) => s.status === "ready").length : 0;

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: [`/studies/${id}`] });
    if (run) await qc.invalidateQueries({ queryKey: [`/studies/${id}/runs/${run}/files`] });
  };

  // the build runs on its own: say in the output window when it ends, and how it went
  const was = useRef(isBuilding);
  useEffect(() => {
    if (was.current && !isBuilding && d) {
      const failed = d.stations.filter((s) => s.status === "failed").length;
      const built = d.stations.filter((s) => s.status === "ready").length;
      (failed ? log.error : log.ok)(t("study.log.buildEnded", { name: d.study.name, built: f(built), failed: f(failed) }));
    }
    was.current = isBuilding;
  }, [isBuilding, d, t, f]);

  const act = async (what: string, path: string, body: unknown = {}, method: "POST" | "PUT" = "POST", done = true) => {
    setBusy(what);
    try {
      const r = await apiWrite(`/studies/${id}${path}`, body, method);
      if (!r.ok) {
        const why = await responseRefusal(t, r);
        log.error(t("study.log.failed", { step: t(`study.step.${what}`), why }));
        toast.error(why);
        return null;
      }
      const out = await r.json();
      setFindings(Array.isArray(out.findings) ? out.findings : []);
      if (done) log.ok(t("study.log.done", { step: t(`study.step.${what}`), name: d?.study.name ?? id }));
      await refresh();
      return out;
    } catch {
      toast.error(t("ui.offline"));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const build = async (quick: boolean) => {
    if (await act("build", "/build", { quick, force: false }, "POST", false)) {
      log.step(t("study.log.buildStarted", { name: d?.study.name ?? id }));
      setBuilding(true);
    }
  };

  const cols: Col<StationRow>[] = useMemo(() => [
    { key: "name", label: t("study.col.station"), value: (s) => s.name, cell: (s) => <strong>{s.name}</strong> },
    { key: "customers", label: t("study.col.customers"), value: (s) => s.customers, numeric: true,
      cell: (s) => (s.customers === null ? "–" : f(s.customers)) },
    { key: "status", label: t("study.col.state"), value: (s) => s.status, cell: (s) => <StationState s={s} /> },
    { key: "gen", label: t("study.col.generation"), value: (s) => s.generation_kw, numeric: true, unit: "kW",
      cell: (s) => (s.generation_kw === null ? "–" : f(s.generation_kw)) },
    { key: "genLimit", label: t("study.col.genLimit"), value: (s) => s.generation_limit,
      cell: (s) => (s.generation_limit ? t(`study.limit.${s.generation_limit}`) : "–") },
    { key: "cons", label: t("study.col.consumption"), value: (s) => s.consumption_kw, numeric: true, unit: "kW",
      cell: (s) => (s.consumption_kw === null ? "–" : f(s.consumption_kw)) },
    { key: "consLimit", label: t("study.col.consLimit"), value: (s) => s.consumption_limit,
      cell: (s) => (s.consumption_limit ? t(`study.limit.${s.consumption_limit}`) : "–") },
    { key: "margin", label: t("study.col.margin"), value: (s) => s.margin_kw, numeric: true, unit: "kW",
      cell: (s) => (s.margin_kw === null ? "–" : f(s.margin_kw)) },
    { key: "seconds", label: t("study.col.seconds"), value: (s) => s.steps.seconds, numeric: true, unit: "s",
      cell: (s) => (s.steps.seconds === null ? "–" : f(Math.round(s.steps.seconds))) },
  ], [t, f]);

  if (detail.isError) {
    return (
      <div className="page-inner study">
        <p className="decision-error" role="alert"><Icon name="alert" size={15} />{t("study.missing", { id })}</p>
        <button className="btn ghost" onClick={() => useStore.getState().setView("start")}>{t("study.toStart")}</button>
      </div>
    );
  }
  if (!d) return <PageSkeleton blocks={2} />;

  const states = Object.entries(d.study.states).map(([k, n]) => t(`study.state.${k}`, { n: f(n) })).join(" · ");

  return (
    <div className="page-inner study">
      <header className="stack">
        <h1 className="headline">{d.study.name}</h1>
        <p className="headline-sub">
          {t("study.lead", { n: f(d.study.stations) })} {states && <span className="caption-muted">{states}</span>}
        </p>
        <p className="caption-muted">{t("study.where", { folder: d.folder, method: d.study.method_version })}</p>
      </header>

      <section className="block stack" aria-labelledby="study-build">
        <div className="block-head">
          <h2 id="study-build">{t("study.build.title")}</h2>
          {isBuilding && <span className="status measured" role="status">{t("study.build.running")}</span>}
        </div>
        <p className="caption">{t("study.build.lead")}</p>
        <div className="answer-actions">
          <button className="btn" disabled={!can.ok || isBuilding || !!busy || d.stations.length === 0} title={can.reason}
            onClick={() => void build(true)}>{t("study.build.quick")}</button>
          <button className="btn ghost" disabled={!can.ok || isBuilding || !!busy || d.stations.length === 0} title={can.reason}
            onClick={() => void build(false)}>{t("study.build.full")}</button>
          {isBuilding && (
            <button className="btn ghost" disabled={!can.ok || !!busy} title={can.reason}
              onClick={() => void act("cancel", "/cancel")}>{t("study.build.stop")}</button>
          )}
          <button className="btn ghost" disabled={!can.ok || isBuilding || !!busy} title={can.reason}
            onClick={() => void act("import", "/import")}>{t("study.build.check")}</button>
        </div>
        {d.log.length > 0 && isBuilding && <pre className="study-log" aria-label={t("study.build.log")}>{d.log.slice(-4).join("\n")}</pre>}
        <RoleNote action="studies" />
      </section>

      <section className="block stack" aria-labelledby="study-stations">
        <div className="block-head"><h2 id="study-stations">{t("study.stations.title")}</h2></div>
        {d.stations.length === 0 ? (
          <p className="caption">{t("study.stations.none")}</p>
        ) : (
          <div className="study-layout">
            <nav className="study-tree" aria-label={t("study.tree")}>
              <ul>
                {d.stations.map((s) => (
                  <li key={s.id}>
                    <button aria-current={picked === s.id || undefined} onClick={() => setPicked(s.id)}>
                      <span className="study-dot" data-state={s.status} aria-hidden="true" />{s.name}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
            <DataTable className="study-table" rows={d.stations} cols={cols} rowKey={(s) => s.id}
              caption={t("study.stations.caption", { n: f(d.stations.length) })} searchLabel={t("study.stations.search")}
              search={d.stations.length > 8} selected={picked} onSelect={(s) => setPicked(s.id)} />
          </div>
        )}
        <p className="caption-muted">{t("study.stations.note")}</p>
      </section>

      <section className="block stack" aria-labelledby="study-run">
        <div className="block-head">
          <h2 id="study-run">{t("study.run.title")}</h2>
          <label className="study-run-pick">
            <span>{t("study.run.pick")}</span>
            <select value={run ?? ""} onChange={(e) => setRunId(e.currentTarget.value)}>
              {runChoices(d).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
        </div>
        <ol className="study-steps">
          {RUN_STEPS.map((step) => (
            <RunStepRow key={step} step={step} done={stepDone(state, step)}
              ready={stepReady(state, step, ready, d.runs.filter((r) => r.run !== run && r.collected).length)}
              busy={busy === step} disabled={!!busy || isBuilding || !run || (step === "sign" ? !canSign.ok : !can.ok)}
              title={step === "sign" ? canSign.reason : can.reason} signer={signer} onSigner={setSigner}
              onRun={() => {
                if (!run) return;
                if (step === "sign") void act("sign", `/runs/${run}/sign`, { by: signer.trim() });
                else void act(step, `/runs/${run}/${step}`);
              }} />
          ))}
        </ol>
        {state?.published && (
          <p className="caption">{state.published === "signerad" ? t("study.run.signed") : t("study.run.draft")}</p>
        )}
        {findings.length > 0 && (
          <ul className="study-findings" aria-label={t("study.run.findings")}>
            {findings.map((x, i) => <li key={i}>{x}</li>)}
          </ul>
        )}
        <RoleNote action="sign_study" />
        {files.data && files.data.files.length > 0 && run && (
          <div className="stack">
            <h3>{t("study.files.title")}</h3>
            <ul className="study-files">
              {files.data.files.map((x) => (
                <li key={x.path}>
                  <a href={`${API}/studies/${id}/runs/${run}/files/${x.path}`} download>
                    <Icon name="download" size={14} />{x.path}
                  </a>
                  <span className="caption-muted num">{f(Math.max(1, Math.round(x.bytes / 1024)))} kB</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}

function StationState({ s }: { s: StationRow }) {
  const t = useT();
  const f = useFmt();
  const kind = STATE_KIND[s.status];
  const label = s.status === "building" && s.steps.running
    ? t("study.state.step", { step: t(`study.buildStep.${s.steps.running}`), done: f(s.steps.done), total: f(s.steps.total) })
    : t(`study.state.${s.status}`, { n: "" }).trim();
  return (
    <span className="study-state" title={s.error ?? undefined}>
      {kind ? <Status kind={kind}>{label}</Status> : <span className="status measured">{label}</span>}
    </span>
  );
}

function RunStepRow({ step, done, ready, busy, disabled, title, signer, onSigner, onRun }: {
  step: RunStep; done: boolean; ready: boolean; busy: boolean; disabled: boolean; title?: string;
  signer: string; onSigner: (v: string) => void; onRun: () => void;
}) {
  const t = useT();
  const signerOk = step !== "sign" || /\S.*,\s*\S/.test(signer);
  return (
    <li className="study-step" data-done={done || undefined}>
      <span className="study-step-mark" aria-hidden="true">{done ? <Icon name="check" size={14} /> : null}</span>
      <div className="study-step-body">
        <strong>{t(`study.step.${step}`)}</strong>
        <span className="caption-muted">{t(`study.stepNote.${step}`)}</span>
        {step === "sign" && (
          <label className="study-signer">
            <span>{t("study.sign.by")}</span>
            <input value={signer} onChange={(e) => onSigner(e.currentTarget.value)} placeholder={t("study.sign.placeholder")}
              autoComplete="name" />
          </label>
        )}
      </div>
      <button className={done ? "btn ghost" : "btn"} disabled={disabled || !ready || !signerOk || busy} title={title} onClick={onRun}>
        {busy ? t("study.step.busy") : done ? t("study.step.again") : t(`study.step.${step}`)}
      </button>
    </li>
  );
}
