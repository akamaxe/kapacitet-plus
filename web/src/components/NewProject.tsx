/**
 * A new project, in three steps: where its network comes from, what the check says of it, and how
 * to build it. The network comes from a DSO's export (the contract's CSV files), from one of the
 * example networks (pandapower's LV benchmarks, synthetic) or from a project file; whichever it
 * is, its tables go through POST /check and then POST /datasets, the one door there is.
 *
 * The build runs in its own process on the server and takes minutes or longer, so these steps end
 * when it starts: the project list shows how it goes, and it can be opened when it is done.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Chip } from "./bits";
import { CheckReport, type Report } from "./CheckReport";
import { Icon } from "./Icon";
import { RoleNote } from "./RoleNote";
import { toast } from "./Toast";
import { API, apiWrite, getJSON, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { refusalOf, refusalText, responseRefusal } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import type { RawNet } from "../lib/projects";

/** Where the network of a new project comes from, as POST /datasets takes it. */
export interface OriginIn {
  kind: "csv" | "template" | "project_file";
  template?: string;
  name?: string;
  synthetic?: boolean;
}

type Files = Record<string, string>;

/**
 * The last step, also offered under a check on the method screen: name the project, choose how
 * much to compute, and start its build.
 */
export function CreateProjectForm({ files, origin, defaultName, onCreated, before }: {
  files: Files;
  origin: OriginIn;
  defaultName?: string;
  onCreated?: (rec: RawNet) => void;
  /** an action that comes before the form's own in its row (the steps' Back) */
  before?: ReactNode;
}) {
  const t = useT();
  const client = useQueryClient();
  const [name, setName] = useState(defaultName ?? "");
  const [quick, setQuick] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const role = useCan("datasets");

  const create = async () => {
    setBusy(true);
    setError(null);
    const r = await apiWrite("/datasets", { name: name.trim() || t("new.defaultName"), files, quick, origin }).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    const rec = (await r.json()) as RawNet;
    await client.invalidateQueries({ queryKey: ["/datasets"] });
    toast.info(t("new.created", { name: rec.name }));
    onCreated?.(rec);
  };

  return (
    <div className="stack new-form">
      <label className="field-inline new-name">
        <span>{t("new.name")}</span>
        <input value={name} maxLength={80} placeholder={t("new.defaultName")} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="seg" role="group" aria-label={t("new.howMuch")}>
        <button type="button" aria-pressed={quick} onClick={() => setQuick(true)}>{t("new.quick")}</button>
        <button type="button" aria-pressed={!quick} onClick={() => setQuick(false)}>{t("new.full")}</button>
      </div>
      <p className="caption">{t(quick ? "new.quickNote" : "new.fullNote")}</p>
      <div className="answer-actions">
        {before}
        <button className="btn" disabled={busy || !role.ok} title={role.reason} onClick={() => void create()}>
          {busy ? t("new.creating") : t("new.create")}
        </button>
      </div>
      <RoleNote action="datasets" />
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
    </div>
  );
}

interface Template {
  id: string;
  name: string;
  customers: number;
  trafo_kva: number;
}

type Source = "csv" | "template" | "file";
const SOURCES: Source[] = ["csv", "template", "file"];
const STEPS = ["source", "check", "build"] as const;

interface Draft {
  files: Files;
  origin: OriginIn;
  /** the name the project is offered, which the last step lets the user change */
  name: string;
  /** what the network is, said above its report */
  label: string;
}

export default function NewProject({ onDone, onCancel, initialFile }: {
  /** the project was made and its build started */
  onDone: (rec: RawNet) => void;
  onCancel: () => void;
  /** a project file chosen from the start column: the steps open on it */
  initialFile?: File | null;
}) {
  const t = useT();
  const f = useFmt();
  const role = useCan("check");
  const contract = useApi<{ tables: { name: string }[] }>("/contract");
  const [source, setSource] = useState<Source>(initialFile ? "file" : "csv");
  const templates = useApi<{ templates: Template[] }>(source === "template" ? "/templates" : null);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ignored, setIgnored] = useState<string[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const names = (contract.data?.tables ?? []).map((tb) => tb.name);

  // each step is a new place: its heading takes the focus, so a screen reader says where it is
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [step]);

  const check = async (next: Draft) => {
    setBusy(true);
    setError(null);
    try {
      const r = await apiWrite("/check", { files: next.files });
      const body = await r.json();
      if (!r.ok) {
        setError(refusalText(t, refusalOf(body), String(r.status)));
        return;
      }
      setDraft(next);
      setReport(body as Report);
      setStep(2);
    } catch {
      setError(t("ui.offline"));
    } finally {
      setBusy(false);
    }
  };

  const fromCsv = async (list: FileList | null) => {
    if (!list?.length) return;
    const files: Files = {};
    const skipped: string[] = [];
    for (const file of Array.from(list)) {
      const name = file.name.toLowerCase().replace(/\.csv$/, "");
      if (names.includes(name)) files[name] = await file.text();
      else skipped.push(file.name);
    }
    setIgnored(skipped);
    if (!Object.keys(files).length) return setError(t("met.imp.noneMatched"));
    await check({ files, origin: { kind: "csv" }, name: "", label: t("new.from.csv") });
  };

  const fromTemplate = async () => {
    const picked = templates.data?.templates.find((x) => x.id === templateId);
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      const doc = await getJSON<{ files: Files }>(`/templates/${picked.id}`);
      await check({ files: doc.files, origin: { kind: "template", template: picked.id }, name: picked.name, label: t("new.from.template", { name: picked.name }) });
    } catch {
      setBusy(false);
      setError(t("ui.offline"));
    }
  };

  const fromFile = async (file: File | null | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const { readProjectFile, ZipError } = await import("../lib/zip");
      try {
        const doc = await readProjectFile(await file.arrayBuffer(), names);
        await check({
          files: doc.files,
          origin: { kind: "project_file", name: file.name, synthetic: doc.synthetic },
          name: doc.name ?? file.name.replace(/\.zip$/i, ""),
          label: t("new.from.file", { name: file.name }),
        });
      } catch (e) {
        setBusy(false);
        setError(e instanceof ZipError ? t(`new.zip.${e.code}`) : t("new.zip.corrupt"));
      }
    } catch {
      setBusy(false);
      setError(t("ui.offline"));
    }
  };

  // a project file chosen in the start column is read as soon as the contract's table names are in
  const opened = useRef(false);
  useEffect(() => {
    if (!initialFile || opened.current || names.length === 0) return;
    opened.current = true;
    void fromFile(initialFile);
    // once, when both are there
  }, [initialFile, names.length]);

  const back = () => {
    setError(null);
    setStep(1);
  };

  return (
    <div className="page-inner start-main new-project">
      <header className="stack">
        <h1 className="headline" tabIndex={-1} ref={heading}>{t("new.title")}</h1>
        <ol className="new-steps" aria-label={t("new.steps")}>
          {STEPS.map((s, i) => (
            <li key={s} aria-current={step === i + 1 ? "step" : undefined} data-done={step > i + 1 || undefined}>
              <span className="num">{f(i + 1)}</span>{t(`new.step.${s}`)}
            </li>
          ))}
        </ol>
      </header>

      {step === 1 && (
        <section className="block stack" aria-labelledby="new-source">
          <div className="block-head"><h2 id="new-source">{t("new.source.title")}</h2></div>
          <div className="seg" role="group" aria-labelledby="new-source">
            {SOURCES.map((s) => (
              <button key={s} type="button" aria-pressed={source === s} onClick={() => { setSource(s); setError(null); }}>{t(`new.source.${s}`)}</button>
            ))}
          </div>

          {source === "csv" && (
            <div className="stack">
              <p className="caption">{t("new.csv.lead")}</p>
              <div className="answer-actions">
                <label className="btn" aria-disabled={busy || !role.ok || undefined} title={role.reason}>
                  <input type="file" accept=".csv,text/csv" multiple className="sr-only" disabled={busy || !role.ok}
                    onChange={(e) => { const list = e.currentTarget.files; void fromCsv(list).finally(() => { e.target.value = ""; }); }} />
                  {t("met.imp.choose")}
                </label>
                <a className="btn ghost" href={`${API}/contract/example.zip`} download><Icon name="download" size={15} />{t("met.imp.example")}</a>
              </div>
              <p className="caption-muted">{t("new.csv.tables", { tables: names.map((n) => `${n}.csv`).join(", ") })}</p>
              {ignored.length > 0 && <p className="caption-muted">{t("met.imp.ignored", { files: ignored.join(", ") })}</p>}
            </div>
          )}

          {source === "template" && (
            <div className="stack">
              <p className="caption">{t("new.template.lead")} <Chip kind="synthetic" /></p>
              <fieldset className="new-templates">
                <legend className="sr-only">{t("new.source.template")}</legend>
                {(templates.data?.templates ?? []).map((x) => (
                  <label key={x.id} data-picked={templateId === x.id || undefined}>
                    <input type="radio" name="template" value={x.id} checked={templateId === x.id} onChange={() => setTemplateId(x.id)} />
                    <span className="nt-name">{x.name}</span>
                    <span className="nt-fact num">{t("proj.d.customers", { n: f(x.customers) })}</span>
                    <span className="nt-fact num">{f(x.trafo_kva)} kVA</span>
                  </label>
                ))}
              </fieldset>
              <div className="answer-actions">
                <button className="btn" disabled={!templateId || busy || !role.ok} title={role.reason} onClick={() => void fromTemplate()}>
                  {t("new.template.use")}
                </button>
              </div>
            </div>
          )}

          {source === "file" && (
            <div className="stack">
              <p className="caption">{t("new.file.lead")}</p>
              <div className="answer-actions">
                <label className="btn" aria-disabled={busy || !role.ok || undefined} title={role.reason}>
                  <input type="file" accept=".zip,application/zip" className="sr-only" disabled={busy || !role.ok}
                    onChange={(e) => { const file = e.currentTarget.files?.[0]; e.currentTarget.value = ""; void fromFile(file); }} />
                  {t("new.file.choose")}
                </label>
              </div>
            </div>
          )}

          <RoleNote action="check" />
          {busy && <p className="caption-muted" role="status">{t("met.imp.checking")}</p>}
          {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
        </section>
      )}

      {step === 2 && draft && report && (
        <section className="block stack" aria-labelledby="new-check">
          <div className="block-head"><h2 id="new-check">{t("new.check.title")}</h2></div>
          <p className="caption">{draft.label}</p>
          <CheckReport report={report} />
          {!report.usable && <p className="caption">{t("new.check.unusable")}</p>}
          <div className="answer-actions">
            <button className="btn ghost" onClick={back}>{t("new.back")}</button>
            <button className="btn" disabled={!report.usable} onClick={() => setStep(3)}>{t("new.continue")}</button>
          </div>
        </section>
      )}

      {step === 3 && draft && (
        <section className="block stack" aria-labelledby="new-build">
          <div className="block-head"><h2 id="new-build">{t("new.build.title")}</h2></div>
          <p className="caption">{draft.label}</p>
          <CreateProjectForm files={draft.files} origin={draft.origin} defaultName={draft.name} onCreated={onDone}
            before={<button className="btn ghost" onClick={() => setStep(2)}>{t("new.back")}</button>} />
        </section>
      )}

      <div>
        <button className="btn ghost small" onClick={onCancel}>{t("new.cancel")}</button>
      </div>
    </div>
  );
}
