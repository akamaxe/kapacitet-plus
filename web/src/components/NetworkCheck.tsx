import { useRef, useState } from "react";
import { CheckReport, type Report } from "./CheckReport";
import { Icon } from "./Icon";
import { CreateProjectForm } from "./NewProject";
import { API, apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { RoleNote } from "./RoleNote";
import { useT } from "../lib/i18n";
import { refusalOf, refusalText } from "../lib/errors";
import { useStore } from "../lib/store";

/**
 * Bring your own network: the four CSV files a DSO exports from its GIS, and the data-quality
 * check that says what is missing before anybody promises a capacity for it. Files are read in
 * the browser and sent as text to POST /check; nothing is stored. A network that passes can be
 * made a project here, as in the start window's own steps.
 */
export function NetworkCheck() {
  const t = useT();
  const contract = useApi<any>("/contract");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const role = useCan("check");
  const [error, setError] = useState<string | null>(null);
  const [ignored, setIgnored] = useState<string[]>([]);
  // the files behind the report on screen, so a usable export can become a network
  const [checked, setChecked] = useState<Record<string, string> | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const tables: any[] = contract.data?.tables ?? [];
  const names = new Set(tables.map((tb) => tb.name));
  const check = async (files: Record<string, string>) => {
    setBusy(true);
    setError(null);
    try {
      const r = await apiWrite("/check", { files });
      const body = await r.json();
      if (!r.ok) setError(refusalText(t, refusalOf(body), String(r.status)));
      else {
        setReport(body);
        setChecked(files);
      }
    } catch {
      setError(t("ui.offline"));
    } finally {
      setBusy(false);
    }
  };

  const fromDisk = async (list: FileList | null) => {
    if (!list?.length) return;
    const files: Record<string, string> = {};
    const skipped: string[] = [];
    for (const file of Array.from(list)) {
      const name = file.name.toLowerCase().replace(/\.csv$/, "");
      if (names.has(name)) files[name] = await file.text();
      else skipped.push(file.name);
    }
    setIgnored(skipped);
    if (Object.keys(files).length) await check(files);
    else setError(t("met.imp.noneMatched"));
  };

  const reference = async () => {
    setIgnored([]);
    setBusy(true);
    try {
      const r = await fetch(`${API}/contract/example`);
      if (!r.ok) throw new Error(String(r.status));
      await check((await r.json()).files);
    } catch {
      setBusy(false);
      setError(t("met.imp.noReference"));
    }
  };

  return (
    <div className="grid-2 netcheck">
      <section className="block stack">
        <div className="block-head"><h3>{t("met.imp.contract")}</h3></div>
        {tables.map((tb) => (
          <div key={tb.name} className="netcheck-table">
            <p><strong>{tb.name}.csv</strong> <span className="caption-muted">{tb.description}</span></p>
            <p className="caption-muted netcheck-cols">
              {tb.columns.map((c: any) => (
                <span key={c.name} className={c.required ? "req" : undefined} title={c.description}>{c.name}</span>
              ))}
            </p>
          </div>
        ))}
        <p className="caption-muted">{t("met.imp.required")}</p>
        <a className="btn ghost small" href={`${API}/contract/example.zip`} download><Icon name="download" size={15} />{t("met.imp.example")}</a>
      </section>

      <section className="block stack" aria-busy={busy}>
        <div className="block-head"><h3>{t("met.imp.check")}</h3></div>
        <p className="caption">{t("met.imp.checkLead")}</p>
        <div className="answer-actions">
          <label className="btn" aria-disabled={!role.ok || undefined} title={role.reason}>
            <input ref={input} type="file" accept=".csv,text/csv" multiple className="sr-only" disabled={!role.ok} onChange={(e) => void fromDisk(e.target.files)} />
            {t("met.imp.choose")}
          </label>
          <button className="btn ghost" disabled={busy || !role.ok} title={role.reason} onClick={reference}>{t("met.imp.reference")}</button>
        </div>
        <RoleNote action="check" />
        {busy && <p className="caption-muted" role="status">{t("met.imp.checking")}</p>}
        {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
        {ignored.length > 0 && <p className="caption-muted">{t("met.imp.ignored", { files: ignored.join(", ") })}</p>}
        {report && (
          <div className="stack">
            <CheckReport report={report} />
            {report.usable && checked && (
              <div className="stack netcreate">
                <h4>{t("new.build.title")}</h4>
                <CreateProjectForm files={checked} origin={{ kind: "csv" }} onCreated={() => useStore.getState().setView("start")} />
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
