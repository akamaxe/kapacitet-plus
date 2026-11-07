import { useRef, useState } from "react";
import { Icon } from "./Icon";
import { Status } from "./bits";
import { CreateNetwork } from "./Networks";
import { API, apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { RoleNote } from "./RoleNote";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { refusalOf, refusalText } from "../lib/errors";

const SEVERITY: Record<string, "critical" | "warning"> = { error: "critical", warning: "warning" };

/**
 * Bring your own network: the four CSV files a DSO exports from its GIS, and the data-quality
 * check that says what is missing before anybody promises a capacity for it. Files are read in
 * the browser and sent as text to POST /check; nothing is stored.
 */
export function NetworkCheck() {
  const t = useT();
  const f = useFmt();
  const contract = useApi<any>("/contract");
  const [report, setReport] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const role = useCan("check");
  const [error, setError] = useState<string | null>(null);
  const [ignored, setIgnored] = useState<string[]>([]);
  // the files behind the report on screen, so a usable export can become a network
  const [checked, setChecked] = useState<Record<string, string> | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const tables: any[] = contract.data?.tables ?? [];
  const names = new Set(tables.map((tb) => tb.name));
  // a check this version has no words for keeps the engine's own name
  const checkLabel = (check: string) => {
    const key = `chk.${check.replace(/ /g, "_")}`;
    const label = t(key);
    return label === key ? check : label;
  };

  // the engine's sentence, said in the reader's language when this version knows its code
  const findingText = (x: { code?: string; params?: Record<string, string | number>; message: string }) => {
    if (!x.code) return x.message;
    const key = `chk.msg.${x.code}`;
    const text = t(key, x.params);
    return text === key ? x.message : text;
  };

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
          <div className="stack" aria-live="polite">
            <div className="netcheck-verdict">
              {report.usable ? <Status kind="good">{t("met.imp.usable")}</Status> : <Status kind="critical">{t("met.imp.unusable")}</Status>}
              <span className="netcheck-score">{t("met.imp.score", { s: f(report.score) })}</span>
            </div>
            <p className="caption-muted">{Object.entries(report.tables ?? {}).map(([k, v]) => `${k}: ${f(v as number)}`).join(" · ")}</p>
            {report.findings.length === 0 ? (
              <p className="caption">{t("met.imp.clean")}</p>
            ) : (
              <ul className="netcheck-findings">
                {report.findings.map((x: any, i: number) => (
                  <li key={i}>
                    {SEVERITY[x.severity]
                      ? <Status kind={SEVERITY[x.severity]}>{t(`met.imp.sev.${x.severity}`)}</Status>
                      : <span className="status measured">{t(`met.imp.sev.${x.severity}`)}</span>}
                    <span><strong>{checkLabel(x.check)}</strong>{x.count ? ` (${f(x.count)})` : ""} — {findingText(x)}</span>
                  </li>
                ))}
              </ul>
            )}
            {report.usable && checked && <CreateNetwork files={checked} />}
          </div>
        )}
      </section>
    </div>
  );
}
