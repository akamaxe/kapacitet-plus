/**
 * A DSO's studies of many stations, as the workbench reads them (GET /api/v1/studies and
 * /studies/{id}, api/studies.py): what the start window lists, what the study's screen shows, and the
 * reading of an archive of contract folders, one per station, that a new study is made from.
 */
import { decodeCsv } from "./decode";
import { readZip, type ZipLimits } from "./zip";

export interface StudySummary {
  id: string;
  /** the DSO's name, which the study's documents carry; the id when it has none */
  name: string;
  stations: number;
  /** how many stations are in each state: imported, building, ready, failed */
  states: Record<string, number>;
  building: boolean;
  method_version: string;
  last_run: string | null;
  site: string | null;
  /** a study whose study.toml cannot be read */
  error?: string;
}

export interface StationRow {
  id: string;
  name: string;
  status: string;
  error: string | null;
  quick: boolean | null;
  customers: number | null;
  steps: { done: number; total: number; running: string | null; seconds: number | null };
  peak_memory_mb: number | null;
  suspended_s: number | null;
  generation_kw: number | null;
  generation_limit: string | null;
  consumption_kw: number | null;
  consumption_limit: string | null;
  margin_kw: number | null;
  validation_status: string | null;
}

export interface RunState {
  run: string;
  collected: boolean;
  reviewed: boolean;
  signed: boolean;
  /** the publication's status: "utkast" (a draft) or "signerad" (signed), or null before one */
  published: string | null;
  compared: boolean;
}

export interface StudyDetail {
  study: StudySummary;
  folder: string;
  stations: StationRow[];
  runs: RunState[];
  current_run: string;
  log: string[];
}

export type RunStep = "collect" | "review" | "sign" | "publish" | "diff";
/** The steps of a run, in the order the study takes them. */
export const RUN_STEPS: RunStep[] = ["collect", "review", "sign", "publish", "diff"];

/** Whether a step of `run` is done, from what its folder holds. */
export function stepDone(run: RunState | undefined, step: RunStep): boolean {
  if (!run) return false;
  return step === "collect" ? run.collected : step === "review" ? run.reviewed : step === "sign" ? run.signed
    : step === "publish" ? run.published !== null : run.compared;
}

/** Whether a step can be taken: each needs the one before it, and a comparison needs this run and
 *  another collected one to compare it with. */
export function stepReady(run: RunState | undefined, step: RunStep, readyStations: number, otherCollected = 0): boolean {
  if (step === "collect") return readyStations > 0;
  if (!run?.collected) return false;
  if (step === "sign") return run.reviewed;
  if (step === "diff") return otherCollected > 0;
  return true;
}

/** The runs to choose from: the study's, with the current period first when it has none yet. */
export function runChoices(detail: StudyDetail): string[] {
  const runs = detail.runs.map((r) => r.run);
  return runs.includes(detail.current_run) ? runs : [detail.current_run, ...runs];
}

export const STUDY_ZIP_LIMITS: ZipLimits = { entries: 4000, bytes: 200 * 2 ** 20 };

export class StudyZipError extends Error {
  constructor(public readonly code: "no_stations" | "nested") {
    super(code);
  }
}

/**
 * An archive of contract folders as {station: {table: text}}: each folder of the archive (at its
 * top, or one level down when the whole archive is one folder) whose files include contract tables
 * is a station, named by the folder. Files that are not tables of the contract are left out; each
 * table is read as UTF-8 or else Windows-1252, as a chosen file is.
 */
export async function stationsFromZip(buf: ArrayBuffer, tables: Iterable<string>): Promise<Record<string, Record<string, string>>> {
  const names = new Set(tables);
  const entries = await readZip(buf, STUDY_ZIP_LIMITS, "path");
  const parts = [...entries.keys()].map((p) => p.replace(/\\/g, "/").split("/").filter(Boolean));
  // an archive of one folder holding the stations' folders: that folder is the top
  const tops = new Set(parts.filter((p) => p.length > 1).map((p) => p[0]));
  const depth = tops.size === 1 && parts.every((p) => p.length >= 3 || p.length === 1) ? 1 : 0;
  const out: Record<string, Record<string, string>> = {};
  for (const [path, bytes] of entries) {
    const p = path.replace(/\\/g, "/").split("/").filter(Boolean).slice(depth);
    if (p.length !== 2) continue;
    const [station, file] = p;
    const table = file.toLowerCase().replace(/\.csv$/, "");
    if (!file.toLowerCase().endsWith(".csv") || !names.has(table)) continue;
    (out[station] ??= {})[table] = decodeCsv(bytes).text;
  }
  if (Object.keys(out).length === 0) throw new StudyZipError("no_stations");
  return out;
}
