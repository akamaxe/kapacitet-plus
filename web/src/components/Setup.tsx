import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CommandLine, Status } from "./bits";
import { Icon } from "./Icon";
import { refusalText } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { reloadAfterRestart, sendBundle, uploadStatus, UploadError, waitUnpacked } from "../lib/upload";

/** The browser is usually on the machine that runs the server, so its system picks the command. */
const ACTIVATE = typeof navigator !== "undefined" && /Win/.test(navigator.userAgent)
  ? ".venv\\Scripts\\activate"
  : "source .venv/bin/activate";

const MB = 2 ** 20;
/** The published data (the repository's release `data`, ODbL): what the setup page asks for. */
const DATA_ZIP = "https://github.com/akamaxe/kapacitet-plus/releases/download/data/kapacitet-data.zip";

/**
 * What a fresh clone shows instead of empty screens: the interface reads what the pipeline
 * computed, and on this machine it has not run yet. The exact commands, in order, and what is
 * missing, from /health.
 *
 * A server that allows it (KAPACITET_SETUP_UPLOAD, as in a codespace) takes the data bundle from
 * the browser instead: for someone who has the data file but has never used a terminal, the
 * whole page becomes one action, and the commands fold away.
 */
export function Setup({ health }: { health: any }) {
  const t = useT();
  const upload = !!health.setup_upload;
  const steps = <SetupSteps health={health} primary={!upload} />;
  return (
    <div className="page">
      <div className="page-inner setup">
        {upload ? <BundleUpload boot={health.boot} /> : (
          <>
            <h1 className="headline">{t("setup.title")}</h1>
            <p className="headline-sub">{t("setup.lead")}</p>
          </>
        )}
        {upload ? (
          <details className="setup-terminal">
            <summary>{t("setup.terminal")}</summary>
            <div className="setup">{steps}</div>
          </details>
        ) : steps}
      </div>
    </div>
  );
}

/** The commands that make the data, what is missing, and a way to look again. */
function SetupSteps({ health, primary }: { health: any; primary: boolean }) {
  const t = useT();
  const client = useQueryClient();
  const parts = Object.entries(health.parts ?? {}) as [string, any][];
  const missing = parts.filter(([, p]) => !p.ready);
  return (
    <>
      <ol className="setup-steps">
        <li>
          <p>{t("setup.env")}</p>
          <CommandLine command={ACTIVATE} />
        </li>
        <li>
          <p>{t("setup.all")}</p>
          <CommandLine command="python -m kapacitet all" />
        </li>
        <li>
          <p>{t("setup.bundle")}</p>
          <CommandLine command="python -m kapacitet unbundle kapacitet-data.zip --sha256" />
        </li>
        <li>
          <p>{t("setup.serve")}</p>
          <CommandLine command="python -m kapacitet serve" />
        </li>
      </ol>
      <section className="block stack">
        <div className="block-head"><h3>{t("setup.missing")}</h3></div>
        <ul className="list caption">
          {missing.map(([part, p]) => (
            <li key={part}><strong>{t(`setup.part.${part}`)}</strong> · {p.missing.join(", ")}{p.command ? <> · <code>{p.command}</code></> : null}</li>
          ))}
        </ul>
      </section>
      <div>
        <button className={primary ? "btn" : "btn ghost"} onClick={() => void client.invalidateQueries()}>
          <Icon name="reset" size={16} />{t("setup.again")}
        </button>
      </div>
    </>
  );
}

type Phase = "idle" | "sending" | "unpacking" | "restarting" | "manual";

/**
 * Load kapacitet-data.zip from the browser: send it in chunks, follow the unpacking, and reload
 * once the server is back on the data. A page reloaded half-way finds the upload where it stopped.
 */
function BundleUpload({ boot }: { boot?: string }) {
  const t = useT();
  const f = useFmt();
  const [phase, setPhase] = useState<Phase>("idle");
  const [sent, setSent] = useState<[number, number]>([0, 0]);
  const [files, setFiles] = useState<[number, number]>([0, 0]);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState<[number, number] | null>(null);
  const alive = useRef(true);

  const say = (e: unknown) => {
    if (!alive.current) return;
    setPhase("idle");
    if (e instanceof UploadError && e.code !== "stopped" && e.code !== "restart_timeout") {
      setError(refusalText(t, { code: e.code, params: e.params, message: e.message }));
    } else {
      setError(t("setup.upload.stopped"));
    }
  };

  // from the end of the upload to the screens: the unpacking, then the server's restart
  const finish = async () => {
    setPhase("unpacking");
    const st = await waitUnpacked((done, total) => alive.current && setFiles([done, total]));
    if (st.restart === "manual") {
      setPhase("manual");
      return;
    }
    setPhase("restarting");
    try {
      await reloadAfterRestart(boot);
    } catch {
      if (alive.current) setPhase("manual");
    }
  };

  // a page reloaded mid-way: an unpacking under way is followed, a stopped upload is offered again
  useEffect(() => {
    alive.current = true;
    uploadStatus()
      .then((st) => {
        if (!alive.current) return;
        if (st.state === "unpacking") void finish().catch(say);
        else if (st.state === "receiving" && st.total) setStopped([st.received, st.total]);
        else if (st.state === "failed" && st.error) setError(refusalText(t, st.error));
      })
      .catch(() => undefined);
    return () => {
      alive.current = false;
    };
    // once, when the page opens
  }, []);

  const start = async (file: File) => {
    setError(null);
    setStopped(null);
    setPhase("sending");
    try {
      await sendBundle(file, (s, total) => alive.current && setSent([s, total]));
      await finish();
    } catch (e) {
      say(e);
    }
  };

  const busy = phase !== "idle";
  const now = phase === "sending" ? t("setup.upload.sendingShort")
    : phase === "unpacking" ? t("setup.upload.starting")
    : phase === "restarting" ? t("setup.upload.restarting")
    : phase === "manual" ? t("setup.upload.manual") : "";
  return (
    <>
      <h1 className="headline">{t("setup.upload.title")}</h1>
      <p className="headline-sub">{t("setup.upload.lead")}</p>
      <div className="setup-upload">
        <div className="answer-actions">
          <label className="btn" aria-disabled={busy || undefined}>
            <Icon name="upload" size={16} />{t("setup.upload.choose")}
            <input type="file" accept=".zip,application/zip" className="sr-only" disabled={busy}
              onChange={(e) => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = "";
                if (file) void start(file);
              }} />
          </label>
          <a className="btn ghost" href={DATA_ZIP} download><Icon name="download" size={16} />{t("setup.upload.download")}</a>
        </div>
        {stopped && !busy && (
          <p className="caption-muted">{t("setup.upload.resume", { sent: f(stopped[0] / MB), total: f(stopped[1] / MB) })}</p>
        )}
        {phase === "sending" && (
          <div className="setup-progress">
            <progress value={sent[0]} max={sent[1] || 1} aria-label={t("setup.upload.sendingShort")} />
            <p className="caption">{t("setup.upload.sending", { sent: f(sent[0] / MB), total: f(sent[1] / MB) })}</p>
          </div>
        )}
        {phase === "unpacking" && (
          <div className="setup-progress">
            <progress value={files[0]} max={files[1] || 1} aria-label={t("setup.upload.starting")} />
            <p className="caption">{files[1] ? t("setup.upload.unpacking", { done: f(files[0]), total: f(files[1]) }) : t("setup.upload.starting")}</p>
          </div>
        )}
        {(phase === "restarting" || phase === "manual") && <p className="caption">{now}</p>}
        {/* what changes, said once per step rather than at every chunk */}
        <p className="sr-only" role="status">{now}</p>
        {error && <p className="caption" role="alert"><Status kind="critical">{error}</Status></p>}
      </div>
    </>
  );
}
