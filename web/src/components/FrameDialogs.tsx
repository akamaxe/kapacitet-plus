/**
 * The frame's three dialogs: the open project's properties, what is computed for it and how it
 * is computed again, and what the program is. Each is opened from a menu and read, then closed;
 * none of them changes anything. Loaded when the first one opens.
 */
import { CommandLine, Missing, Status } from "./bits";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { Logo } from "./Logo";
import { ProjectFacts, useKind } from "./ProjectFacts";
import { Bar } from "./Skeleton";
import { useApi } from "../lib/api";
import { dateTime, duration, useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { DATA_LICENSE, LICENSE, REPOSITORY } from "../lib/links";
import { projectFileUrl, projectsOf, useProjects } from "../lib/projects";
import { useStore } from "../lib/store";

export type FrameDialog = "properties" | "computation" | "about";

const TITLE = "frame-dialog-title";

function Loading() {
  return <div className="stack" aria-busy="true"><Bar w="60%" /><Bar /><Bar w="80%" /></div>;
}

function Close({ onClose, children }: { onClose: () => void; children?: React.ReactNode }) {
  const t = useT();
  return (
    <div className="decision-actions">
      {children}
      <button className="btn" onClick={onClose}>{t("ui.close")}</button>
    </div>
  );
}

/** What the open project is: its name, where it came from and the data sheet the start window shows. */
function Properties({ onClose }: { onClose: () => void }) {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const list = useProjects();
  const kind = useKind();
  const p = list.data ? projectsOf(list.data).find((x) => x.current) : undefined;
  return (
    <div className="sheet frame-sheet">
      <h2 id={TITLE}>{p?.name ?? t("props.title")}</h2>
      {list.isError ? <Missing of={[list]} /> : !p ? <Loading /> : (
        <>
          <p className="caption">{kind(p)}</p>
          <ProjectFacts p={p} />
          <dl className="kv">
            <dt>{t("props.id")}</dt><dd><code>{p.id}</code></dd>
            {p.created && <><dt>{t("props.created")}</dt><dd>{dateTime(lang, p.created)}</dd></>}
            {p.origin.generator && <><dt>{t("props.generator")}</dt><dd><code>{p.origin.generator}</code></dd></>}
          </dl>
          <Close onClose={onClose}>
            {p.state !== "empty" && (
              <a className="btn ghost" href={projectFileUrl(p.id)} download>
                <Icon name="download" size={15} />{t("proj.export")}
              </a>
            )}
          </Close>
        </>
      )}
    </div>
  );
}

const PARTS = ["network", "results", "extra", "requests"] as const;

/**
 * What is computed for the project in use, when, and the command that computes each part again.
 * The studies run from the command line; this says so instead of offering a button that would
 * hold the browser for an hour.
 */
function Computation({ onClose, onStart }: { onClose: () => void; onStart: () => void }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const health = useApi<any>("/health");
  const meta = useApi<any>("/meta");
  const run = meta.data?.run;
  const imported = !!meta.data?.dataset?.imported;
  const years: number[] = run?.years ?? [];
  return (
    <div className="sheet frame-sheet">
      <h2 id={TITLE}>{t("comp.title")}</h2>
      <p className="caption">{t(imported ? "comp.leadBuild" : "comp.lead")}</p>
      {health.isError || meta.isError ? <Missing of={[health, meta]} /> : !health.data || !meta.data ? <Loading /> : (
        <>
          <dl className="kv">
            <dt>{t("comp.lastRun")}</dt>
            <dd>
              {run?.computed_at
                ? t("comp.lastRunValue", {
                    when: dateTime(lang, run.computed_at),
                    mode: t(run.quick ? "comp.mode.quick" : "comp.mode.full"),
                    years: f(years.length),
                    took: duration(lang, run.seconds),
                  })
                : t("comp.never")}
            </dd>
            {meta.data.results_updated_at && (
              <><dt>{t("comp.updated")}</dt><dd>{dateTime(lang, meta.data.results_updated_at)}</dd></>
            )}
          </dl>
          <table className="data comp-table">
            <thead>
              <tr><th scope="col">{t("comp.col.part")}</th><th scope="col">{t("comp.col.state")}</th><th scope="col">{t("comp.col.command")}</th></tr>
            </thead>
            <tbody>
              {PARTS.map((part) => {
                const info = health.data.parts?.[part];
                if (!info) return null;
                return (
                  <tr key={part}>
                    <th scope="row">{t(`setup.part.${part}`)}</th>
                    <td>
                      {info.ready
                        ? <span className="status measured">{t("comp.ready")}</span>
                        : <Status kind="warning">{t("comp.missing", { files: info.missing.join(", ") })}</Status>}
                    </td>
                    <td>{info.command ? <CommandLine command={info.command} /> : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Close onClose={onClose}>
            {imported && <button className="btn ghost" onClick={() => { onClose(); onStart(); }}>{t("start.go")}</button>}
          </Close>
        </>
      )}
    </div>
  );
}

/** What the program is, its version, its licences and the data it owes a credit for. */
function About({ onClose }: { onClose: () => void }) {
  const t = useT();
  const health = useApi<any>("/health");
  return (
    <div className="sheet frame-sheet about">
      <h2 id={TITLE} className="about-logo"><Logo tone="ground" height={34} /></h2>
      <p>{t("about.what")}</p>
      <dl className="kv">
        <dt>{t("about.version")}</dt><dd className="num">{health.data?.version ?? "–"}</dd>
        <dt>{t("about.licence")}</dt>
        <dd>
          <a href={LICENSE} target="_blank" rel="noopener noreferrer">{t("about.licence.code")}</a>
          {" · "}
          <a href={DATA_LICENSE} target="_blank" rel="noopener noreferrer">{t("about.licence.data")}</a>
        </dd>
        <dt>{t("about.source")}</dt>
        <dd><a href={REPOSITORY} target="_blank" rel="noopener noreferrer">{REPOSITORY.replace("https://", "")}</a></dd>
      </dl>
      <p className="source-line">{t("about.credits")}</p>
      <Close onClose={onClose} />
    </div>
  );
}

export default function FrameDialogs({ which, onClose, onStart }: {
  which: FrameDialog | null; onClose: () => void;
  /** to the start window, where a project is built again */
  onStart: () => void;
}) {
  return (
    <Dialog open={which !== null} onClose={onClose} labelledBy={TITLE} width={which === "computation" ? 760 : which === "about" ? 480 : 620}>
      {which === "properties" && <Properties onClose={onClose} />}
      {which === "computation" && <Computation onClose={onClose} onStart={onStart} />}
      {which === "about" && <About onClose={onClose} />}
    </Dialog>
  );
}
