/**
 * The start window: what one sees on entering, as in the tools a network planner works in every
 * day. The projects on this server as a table, the one picked as a data sheet, and one action to
 * open it. A project is a network with what was computed for it (lib/projects.ts); the one the
 * server is on opens at once, another one restarts the server on its folder and so asks first.
 */
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Chip, Missing, Status, Tile } from "../components/bits";
import { DataTable, type Col } from "../components/DataTable";
import { Dialog } from "../components/Dialog";
import { Icon } from "../components/Icon";
import { RoleNote } from "../components/RoleNote";
import { PageSkeleton } from "../components/Skeleton";
import { toast } from "../components/Toast";
import { useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { dateTime, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import {
  buildProgress, enterWhenBack, forgetRecent, lastScreen, projectFileUrl, projectWrite, projectsOf, readRecents,
  sortProjects, touchRecent, useProjects, type Project,
} from "../lib/projects";
import { useStore } from "../lib/store";

const NewProject = lazy(() => import("../components/NewProject"));

/** The manual is a document of the repository, in Swedish; the interface links to it. */
const MANUAL = "https://github.com/akamaxe/kapacitet-plus/blob/main/docs/manual_sv.md";

/** A list this long gets a search field; a shorter one is read at a glance. */
const SEARCH_FROM = 6;

/** A network is synthetic when it was made from an example network, or its project file says so. */
export function isSynthetic(p: Project): boolean {
  return p.origin.kind === "template" || p.origin.synthetic === true;
}

/** What the project is, in one sentence under its name. */
function useKind() {
  const t = useT();
  return (p: Project): string => {
    if (p.reference) return t("proj.kind.reference");
    if (p.origin.kind === "template") return t("proj.kind.template", { name: p.origin.name ?? p.origin.template ?? "" });
    if (p.origin.kind === "project_file") return t(p.origin.synthetic ? "proj.kind.fileSynthetic" : "proj.kind.file");
    return t("proj.kind.csv");
  };
}

/** How the build stands, in words: the step running, or where and why it stopped. */
function useBuildLine() {
  const t = useT();
  const f = useFmt();
  return (p: Project): string => {
    const b = buildProgress(p.steps);
    if (p.state === "failed") {
      return b.failed ? t("proj.failedAt", { step: t(`proj.step.${b.failed.name}`), error: b.failed.error ?? p.error ?? "" }) : (p.error ?? "");
    }
    if (p.state === "building") {
      return b.running
        ? t("proj.steps", { done: f(b.done), of: f(b.total), step: t(`proj.step.${b.running.name}`) })
        : t("proj.stepsStarting", { of: f(b.total) });
    }
    return "";
  };
}

/** The last lines a build wrote, for a project that is being built or whose build stopped. */
function BuildLog({ id, live }: { id: string; live: boolean }) {
  const t = useT();
  const rec = useApi<{ log?: string[] }>(`/datasets/${id}`, { refetchMs: live ? 3000 : undefined });
  const lines = (rec.data?.log ?? []).filter((ln) => ln.trim()).slice(-8);
  if (lines.length === 0) return null;
  return (
    <details className="proj-log">
      <summary>{t("proj.log")}</summary>
      <pre className="payload" tabIndex={0}>{lines.join("\n")}</pre>
    </details>
  );
}

export default function Start({ onKeys }: {
  /** opens the key sheet, which belongs to the frame */
  onKeys: () => void;
}) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const client = useQueryClient();
  const list = useProjects();
  const role = useCan("datasets");
  const kind = useKind();
  const buildLine = useBuildLine();
  const [selected, setSelected] = useState<string | null>(null);
  const [switchTo, setSwitchTo] = useState<Project | null>(null);
  const [switching, setSwitching] = useState(false);
  const [toDelete, setToDelete] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  // the list, or the steps that make a project (opened on a project file when one was chosen)
  const [making, setMaking] = useState<{ file: File | null } | null>(null);

  const projects = useMemo(() => (list.data ? sortProjects(projectsOf(list.data), readRecents()) : []), [list.data]);
  const picked = projects.find((p) => p.id === selected) ?? projects.find((p) => p.current) ?? projects[0] ?? null;
  const supervised = !!list.data?.supervised;

  // the server comes back on the other project: the workbench loads anew once /health says so
  useEffect(() => {
    if (!switching || !switchTo) return;
    return enterWhenBack(switchTo.id);
  }, [switching, switchTo]);

  const refresh = () => client.invalidateQueries({ queryKey: ["/datasets"] });

  const open = (p: Project) => {
    if (p.state !== "ready") return;
    if (p.current) {
      touchRecent(p.id);
      useStore.getState().setScreen(lastScreen());
      return;
    }
    if (!supervised || !role.ok) return;
    setError(null);
    setSwitchTo(p);
  };

  const confirmSwitch = async () => {
    if (!switchTo) return;
    setError(null);
    const r = await projectWrite(switchTo.id, "activate");
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    setSwitching(true);
  };

  const act = async (p: Project, action: "build" | "cancel") => {
    const r = await projectWrite(p.id, action);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await refresh();
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    const p = toDelete;
    const r = await projectWrite(p.id, "delete");
    setToDelete(null);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    forgetRecent(p.id);
    if (selected === p.id) setSelected(null);
    await refresh();
    toast.info(t("proj.deleted", { name: p.name }));
  };

  const tour = () => {
    useStore.getState().setScreen(lastScreen());
    useStore.getState().setTour(0);
  };

  const cols: Col<Project>[] = [
    {
      key: "name",
      label: t("start.col.name"),
      value: (p) => p.name,
      cell: (p) => (
        <span className="proj-name">
          <strong>{p.name}</strong>
          {p.current && <Status kind="good">{t("proj.inUse")}</Status>}
          {isSynthetic(p) && <Chip kind="synthetic" />}
        </span>
      ),
    },
    { key: "customers", label: t("start.col.customers"), value: (p) => p.customers, numeric: true, cell: (p) => (p.customers === null ? "–" : f(p.customers)) },
    { key: "trafo", label: t("start.col.trafo"), value: (p) => p.summary.trafo_type },
    {
      key: "results",
      label: t("start.col.results"),
      value: (p) => p.summary.computed_at ?? p.state,
      cell: (p) =>
        p.state === "ready" ? (
          <span className="num">
            {p.summary.computed_at ? dateTime(lang, p.summary.computed_at, false) : t("proj.state.ready")}
            {p.quick ? <span className="caption-muted"> · {t("proj.quick")}</span> : null}
          </span>
        ) : p.state === "failed" ? (
          <Status kind="critical">{t("proj.state.failed")}</Status>
        ) : (
          <span className="status measured">{t(`proj.state.${p.state}`)}</span>
        ),
    },
  ];

  const ready = picked?.state === "ready";
  const canSwitch = supervised && role.ok && !switching;
  const years = picked?.summary.years;
  const yearsText = years ? (years[0] === years[1] ? String(years[0]) : `${years[0]}–${years[1]}`) : "–";

  return (
    <div className="start">
      <aside className="start-side" aria-label={t("start.side")}>
        <h2>{t("start.title")}</h2>
        <div className="start-actions">
          <button className="btn ghost" aria-pressed={making !== null} disabled={!role.ok} title={role.reason} onClick={() => setMaking({ file: null })}>
            <Icon name="plus" size={16} />{t("start.new")}
          </button>
          <label className="btn ghost" aria-disabled={!role.ok || undefined} title={role.reason}>
            <input type="file" accept=".zip,application/zip" className="sr-only" disabled={!role.ok}
              onChange={(e) => { const file = e.currentTarget.files?.[0] ?? null; e.currentTarget.value = ""; if (file) setMaking({ file }); }} />
            <Icon name="folder" size={16} />{t("start.openFile")}
          </label>
        </div>
        <nav aria-label={t("start.help")}>
          <h3>{t("start.help")}</h3>
          <ul className="start-links">
            {projects.some((p) => p.current && p.state === "ready") && (
              <li><button onClick={tour}>{t("tour.start")}</button></li>
            )}
            <li><button onClick={onKeys} aria-haspopup="dialog">{t("keys.title")}<kbd>?</kbd></button></li>
            <li><a href={MANUAL} target="_blank" rel="noopener noreferrer">{t("start.manual")}</a></li>
          </ul>
        </nav>
      </aside>

      <div className="page">
        {making ? (
          <Suspense fallback={<PageSkeleton blocks={1} />}>
            <NewProject
              key={making.file ? `file:${making.file.name}:${making.file.size}` : "new"}
              initialFile={making.file}
              onCancel={() => setMaking(null)}
              onDone={(rec) => { setMaking(null); setSelected(rec.id); void refresh(); }}
            />
          </Suspense>
        ) : (
        <div className="page-inner start-main">
          <header className="stack">
            <h1 className="headline">{t("start.projects")}</h1>
            <p className="headline-sub">{t("start.lead")}</p>
          </header>

          {list.isError ? <Missing of={[list]} /> : !list.data ? <PageSkeleton blocks={1} /> : (
            <>
              <DataTable
                className="start-table"
                rows={projects}
                cols={cols}
                rowKey={(p) => p.id}
                caption={t("start.caption", { n: f(projects.length) })}
                searchLabel={t("start.search")}
                search={projects.length > SEARCH_FROM}
                selected={picked?.id ?? null}
                onSelect={(p) => setSelected(p.id)}
                onOpen={open}
              />

              {picked && (
                <section className="block proj-detail" aria-labelledby="proj-title">
                  <div className="block-head">
                    <h2 id="proj-title">{picked.name}</h2>
                    {picked.current ? <Status kind="good">{t("proj.inUse")}</Status>
                      : picked.state === "failed" ? <Status kind="critical">{t("proj.state.failed")}</Status>
                      : <span className="status measured">{t(`proj.state.${picked.state}`)}</span>}
                  </div>
                  <p className="caption">{kind(picked)}</p>
                  {(picked.state === "building" || picked.state === "failed") && (
                    <p className={picked.state === "failed" ? "decision-error" : "caption"} role="status">
                      {picked.state === "failed" && <Icon name="alert" size={15} />}{buildLine(picked)}
                    </p>
                  )}
                  {!picked.reference && (picked.state === "building" || picked.state === "failed") && (
                    <BuildLog id={picked.id} live={picked.state === "building"} />
                  )}
                  <div className="tiles">
                    <Tile
                      label={t("proj.d.network")}
                      value={picked.customers === null ? "–" : <span className="t-word">{
                        picked.summary.feeders === null
                          ? t("proj.d.customers", { n: f(picked.customers) })
                          : t("proj.d.networkValue", { n: f(picked.customers), feeders: f(picked.summary.feeders) })
                      }</span>}
                    />
                    <Tile label={t("proj.d.trafo")} value={<span className="t-word">{picked.summary.trafo_type ?? "–"}</span>} />
                    <Tile
                      label={t("proj.d.static")}
                      value={picked.summary.static_kw === null ? "–" : f(picked.summary.static_kw)}
                      unit={picked.summary.static_kw === null ? undefined : "kW"}
                    />
                    <Tile
                      label={t("proj.d.hourly")}
                      value={picked.summary.hourly_kw === null ? "–" : f(picked.summary.hourly_kw)}
                      unit={picked.summary.hourly_kw === null ? undefined : "kW"}
                    />
                    <Tile
                      label={t("proj.d.years")}
                      mark={years ? "real" : undefined}
                      value={<span className="t-word">{yearsText}</span>}
                      note={picked.quick ? t("proj.d.quickNote") : undefined}
                    />
                    <Tile
                      label={t("proj.d.computed")}
                      value={<span className="t-word">{picked.summary.computed_at ? dateTime(lang, picked.summary.computed_at) : "–"}</span>}
                    />
                  </div>
                  {(picked.summary.static_kw !== null || picked.summary.hourly_kw !== null) && (
                    <p className="source-line">{t("proj.d.capacityNote")}</p>
                  )}

                  <div className="answer-actions proj-actions">
                    <button
                      className="btn"
                      disabled={!ready || (!picked.current && !canSwitch)}
                      title={!picked.current ? role.reason : undefined}
                      onClick={() => open(picked)}
                    >
                      {t("proj.open")}
                    </button>
                    {!picked.reference && (picked.state === "failed" || picked.state === "waiting") && (
                      <button className="btn ghost" disabled={switching || !role.ok} title={role.reason} onClick={() => void act(picked, "build")}>
                        {t("proj.rebuild")}
                      </button>
                    )}
                    {picked.state === "building" && (
                      <button className="btn ghost" disabled={!role.ok} title={role.reason} onClick={() => void act(picked, "cancel")}>
                        {t("proj.stop")}
                      </button>
                    )}
                    {picked.state !== "empty" && (
                      <a className="btn ghost" href={projectFileUrl(picked.id)} download>
                        <Icon name="download" size={15} />{t("proj.export")}
                      </a>
                    )}
                    {!picked.reference && !picked.current && picked.state !== "building" && (
                      <button className="btn ghost danger" disabled={switching || !role.ok} title={role.reason} onClick={() => setToDelete(picked)}>
                        {t("proj.delete")}
                      </button>
                    )}
                  </div>
                  <RoleNote action="datasets" />
                  {!ready && picked.state !== "empty" && <p className="caption-muted">{t("proj.notReady")}</p>}
                  {ready && !picked.current && !supervised && <p className="caption-muted">{t("proj.unsupervised")}</p>}
                  {picked.current && !picked.reference && <p className="caption-muted">{t("proj.shared")}</p>}
                </section>
              )}
            </>
          )}
        </div>
        )}
      </div>

      <Dialog open={switchTo !== null} onClose={() => { if (!switching) setSwitchTo(null); }} labelledBy="proj-switch-title" width={480}>
        {switchTo && (
          <div className="sheet">
            <h2 id="proj-switch-title">{t("proj.switch.title", { name: switchTo.name })}</h2>
            <p>{t("proj.switch.body")}</p>
            {switching ? <p role="status">{t("proj.switching", { name: switchTo.name })}</p> : (
              <div className="decision-actions">
                <button className="btn ghost" onClick={() => setSwitchTo(null)}>{t("ui.cancel")}</button>
                <button className="btn" onClick={() => void confirmSwitch()}>{t("proj.open")}</button>
              </div>
            )}
            {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
          </div>
        )}
      </Dialog>

      <Dialog open={toDelete !== null} onClose={() => setToDelete(null)} labelledBy="proj-delete-title" width={460}>
        {toDelete && (
          <div className="sheet">
            <h2 id="proj-delete-title">{t("proj.delete.title", { name: toDelete.name })}</h2>
            <p>{t("proj.delete.body")}</p>
            <div className="decision-actions">
              <button className="btn ghost" onClick={() => setToDelete(null)}>{t("ui.cancel")}</button>
              <button className="btn ghost danger" onClick={() => void confirmDelete()}>{t("proj.delete.go")}</button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
