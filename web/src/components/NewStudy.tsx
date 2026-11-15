/**
 * A new study of many stations, from the start window (POST /api/v1/studies): the demonstration of
 * three synthetic stations, an empty study, contract folders (a .zip with one folder of CSV tables
 * per station, read here) or CGMES files with the table that names each consumer's customer and
 * fuse. The study is made in the studies' folder; one from files is imported and checked at once.
 */
import { useState } from "react";
import { Chip } from "./bits";
import { Icon } from "./Icon";
import { RoleNote } from "./RoleNote";
import { apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { readCsvFile } from "../lib/decode";
import { responseRefusal } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { log } from "../lib/log";
import { StudyZipError, stationsFromZip } from "../lib/studies";

type Source = "demo" | "empty" | "contract" | "cgmes";
const SOURCES: Source[] = ["demo", "empty", "contract", "cgmes"];

interface Made {
  study: { id: string; name: string; stations: number };
  reports: Record<string, { usable: boolean }>;
}

export default function NewStudy({ onDone, onCancel }: { onDone: (id: string) => void; onCancel: () => void }) {
  const t = useT();
  const f = useFmt();
  const role = useCan("studies");
  const contract = useApi<{ tables: { name: string }[] }>("/contract");
  const tables = (contract.data?.tables ?? []).map((x) => x.name);
  const [source, setSource] = useState<Source>("demo");
  const [name, setName] = useState("");
  const [stations, setStations] = useState<Record<string, Record<string, string>> | null>(null);
  const [cgmes, setCgmes] = useState<Record<string, string> | null>(null);
  const [customers, setCustomers] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fromZip = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      setStations(await stationsFromZip(await file.arrayBuffer(), tables));
    } catch (e) {
      const { ZipError } = await import("../lib/zip");
      setStations(null);
      setError(e instanceof StudyZipError ? t("newStudy.noStations") : e instanceof ZipError ? t(`new.zip.${e.code}`) : t("new.zip.corrupt"));
    }
  };

  const fromCgmes = async (list: FileList | null) => {
    if (!list?.length) return;
    const out: Record<string, string> = {};
    for (const file of Array.from(list)) out[file.name] = await file.text();
    setCgmes(out);
  };

  const ready = source === "demo" || source === "empty" || (source === "contract" && !!stations)
    || (source === "cgmes" && !!cgmes && customers.trim() !== "");

  const create = async () => {
    setBusy(true);
    setError(null);
    const body = source === "contract" ? { kind: source, name, stations }
      : source === "cgmes" ? { kind: source, name, cgmes, customers } : { kind: source, name };
    try {
      const r = await apiWrite("/studies", body);
      if (!r.ok) {
        setError(await responseRefusal(t, r));
        return;
      }
      const out = (await r.json()) as Made;
      const usable = Object.values(out.reports).filter((x) => x.usable).length;
      log.ok(t("newStudy.made", { name: out.study.name, n: f(out.study.stations) }));
      if (Object.keys(out.reports).length > usable) log.error(t("newStudy.unusable", { n: f(Object.keys(out.reports).length - usable) }));
      onDone(out.study.id);
    } catch {
      setError(t("ui.offline"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-inner start-main new-project">
      <header className="stack">
        <h1 className="headline">{t("newStudy.title")}</h1>
        <p className="headline-sub">{t("newStudy.lead")}</p>
      </header>
      <section className="block stack" aria-labelledby="new-study-source">
        <div className="block-head"><h2 id="new-study-source">{t("newStudy.source")}</h2></div>
        <div className="seg" role="group" aria-labelledby="new-study-source">
          {SOURCES.map((s) => (
            <button key={s} type="button" aria-pressed={source === s} onClick={() => { setSource(s); setError(null); }}>
              {t(`newStudy.source.${s}`)}
            </button>
          ))}
        </div>
        <p className="caption">{t(`newStudy.${source}.lead`)} {source === "demo" && <Chip kind="synthetic" />}</p>

        {source !== "demo" && (
          <label className="new-name">
            <span>{t("newStudy.name")}</span>
            <input value={name} maxLength={80} onChange={(e) => setName(e.currentTarget.value)} placeholder={t("newStudy.namePlaceholder")} />
          </label>
        )}

        {source === "contract" && (
          <div className="stack">
            <div className="answer-actions">
              <label className="btn" aria-disabled={busy || undefined}>
                <input type="file" accept=".zip,application/zip" className="sr-only" disabled={busy}
                  onChange={(e) => { const file = e.currentTarget.files?.[0]; e.currentTarget.value = ""; void fromZip(file); }} />
                <Icon name="folder" size={16} />{t("newStudy.chooseZip")}
              </label>
            </div>
            {stations && (
              <p className="caption">{t("newStudy.stations", { n: f(Object.keys(stations).length), names: Object.keys(stations).slice(0, 6).join(", ") })}</p>
            )}
          </div>
        )}

        {source === "cgmes" && (
          <div className="stack">
            <div className="answer-actions">
              <label className="btn" aria-disabled={busy || undefined}>
                <input type="file" accept=".xml,.rdf" multiple className="sr-only" disabled={busy}
                  onChange={(e) => { const list = e.currentTarget.files; void fromCgmes(list).finally(() => { e.target.value = ""; }); }} />
                <Icon name="folder" size={16} />{t("newStudy.chooseCgmes")}
              </label>
              <label className="btn ghost" aria-disabled={busy || undefined}>
                <input type="file" accept=".csv,text/csv" className="sr-only" disabled={busy}
                  onChange={(e) => { const file = e.currentTarget.files?.[0]; e.currentTarget.value = ""; if (file) void readCsvFile(file).then((d) => setCustomers(d.text)); }} />
                {t("newStudy.chooseCustomers")}
              </label>
            </div>
            {cgmes && <p className="caption">{t("newStudy.cgmesFiles", { names: Object.keys(cgmes).join(", ") })}</p>}
            {customers && <p className="caption">{t("newStudy.customersRead", { n: f(Math.max(0, customers.trim().split(/\r?\n/).length - 1)) })}</p>}
          </div>
        )}

        {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
        <div className="answer-actions">
          <button className="btn ghost" onClick={onCancel}>{t("newStudy.cancel")}</button>
          <button className="btn" disabled={!ready || busy || !role.ok} title={role.reason} onClick={() => void create()}>
            {busy ? t("newStudy.creating") : t("newStudy.create")}
          </button>
        </div>
        <RoleNote action="studies" />
      </section>
    </div>
  );
}
