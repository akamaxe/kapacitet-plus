/**
 * Projects: what the start window lists and opens.
 *
 * A project is one network with what was computed for it. The server calls them networks
 * (`/datasets`, src/kapacitet/datasets.py): the reference network, always there, and every network
 * made from an export, an example network or a project file, each in its own folder. The server
 * shows one at a time; opening another restarts it on that folder, so it always asks first.
 */
import { API, apiWrite, useApi, type WriteMethod } from "./api";
import { OPENING, SCREENS, type Screen } from "./store";

/** What a project list shows of a network, read from the network's own files (null: not there). */
export interface Summary {
  customers: number | null;
  feeders: number | null;
  buses: number | null;
  lines: number | null;
  trafo_type: string | null;
  computed_at: string | null;
  quick: boolean | null;
  reference_year: number | null;
  /** the first and the last weather year computed */
  years: [number, number] | null;
  n_years: number | null;
  /** medians over the adoption orders, in kW AC */
  static_kw: number | null;
  hourly_kw: number | null;
}

export const NO_SUMMARY: Summary = {
  customers: null, feeders: null, buses: null, lines: null, trafo_type: null, computed_at: null, quick: null,
  reference_year: null, years: null, n_years: null, static_kw: null, hourly_kw: null,
};

export type OriginKind = "reference" | "csv" | "template" | "project_file";

/** Where a network came from (datasets.clean_origin). */
export interface Origin {
  kind: OriginKind;
  template?: string;
  name?: string;
  generator?: string;
  synthetic?: boolean;
  synthetic_parts?: string[];
}

export interface BuildStep {
  name: string;
  status: string;
  error?: string;
}

/** A network as the server lists it. */
export interface RawNet {
  id: string;
  name: string;
  created: string;
  status: "uploaded" | "building" | "ready" | "failed";
  customers?: number;
  quick?: boolean;
  error?: string;
  building?: boolean;
  steps?: BuildStep[];
  summary?: Partial<Summary>;
  origin?: Origin;
}

export interface Listing {
  /** the network this server process runs on, and the one it will run on after a restart */
  current: string;
  active: string;
  /** started with `kapacitet serve`, so it can restart on another network */
  supervised: boolean;
  reference: { id: string; name: string; summary?: Partial<Summary> };
  items: RawNet[];
}

/**
 * `ready` can be opened; `building` and `waiting` have a build under way or due; `failed` stopped;
 * `empty` is the reference network before anything was computed for it.
 */
export type ProjectState = "ready" | "building" | "waiting" | "failed" | "empty";

export interface Project {
  id: string;
  name: string;
  reference: boolean;
  /** the one the server is on */
  current: boolean;
  state: ProjectState;
  created: string | null;
  quick: boolean | null;
  customers: number | null;
  error?: string;
  steps: BuildStep[];
  summary: Summary;
  origin: Origin;
}

/** The reference network and every other one, as projects. Pure. */
export function projectsOf(listing: Listing): Project[] {
  const refSummary: Summary = { ...NO_SUMMARY, ...(listing.reference.summary ?? {}) };
  const reference: Project = {
    id: listing.reference.id,
    name: listing.reference.name,
    reference: true,
    current: listing.current === listing.reference.id,
    state: refSummary.computed_at || refSummary.static_kw !== null ? "ready" : "empty",
    created: null,
    quick: refSummary.quick,
    customers: refSummary.customers,
    steps: [],
    summary: refSummary,
    origin: { kind: "reference" },
  };
  const others = listing.items.map((n): Project => {
    const summary: Summary = { ...NO_SUMMARY, ...(n.summary ?? {}) };
    const state: ProjectState =
      n.status === "building" || n.building ? "building" : n.status === "ready" ? "ready" : n.status === "failed" ? "failed" : "waiting";
    return {
      id: n.id,
      name: n.name,
      reference: false,
      current: n.id === listing.current,
      state,
      created: n.created || null,
      quick: n.quick ?? summary.quick,
      customers: summary.customers ?? n.customers ?? null,
      error: n.error,
      steps: n.steps ?? [],
      summary,
      origin: n.origin ?? { kind: "csv" },
    };
  });
  return [reference, ...others];
}

/** The steps of a build as `done of total` and the one running or failed. Pure. */
export function buildProgress(steps: BuildStep[]): { done: number; total: number; running?: BuildStep; failed?: BuildStep } {
  return {
    done: steps.filter((s) => s.status === "done").length,
    total: steps.length,
    running: steps.find((s) => s.status === "running"),
    failed: steps.find((s) => s.status === "failed"),
  };
}

// ---------------------------------------------------------------- what this browser opened last

const RECENT = "kap.recent";

/** When each project was last opened from this browser, by id. */
export function readRecents(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    const out: Record<string, number> = {};
    for (const [id, at] of Object.entries(raw)) if (typeof at === "number" && Number.isFinite(at)) out[id] = at;
    return out;
  } catch {
    return {};
  }
}

export function touchRecent(id: string, now: number = Date.now()): void {
  try {
    localStorage.setItem(RECENT, JSON.stringify({ ...readRecents(), [id]: now }));
  } catch {
    /* private mode: the list simply keeps the server's order */
  }
}

export function forgetRecent(id: string): void {
  try {
    const rest = readRecents();
    delete rest[id];
    localStorage.setItem(RECENT, JSON.stringify(rest));
  } catch {
    /* private mode */
  }
}

/** The one in use first, then the ones opened most recently here, then the newest. Pure. */
export function sortProjects(projects: Project[], recent: Record<string, number>): Project[] {
  return [...projects].sort(
    (a, b) =>
      Number(b.current) - Number(a.current) ||
      (recent[b.id] ?? 0) - (recent[a.id] ?? 0) ||
      (b.created ?? "").localeCompare(a.created ?? "") ||
      a.name.localeCompare(b.name),
  );
}

// ---------------------------------------------------------------- the server

/** The projects, asked again every three seconds while one of them is being built. */
export function useProjects() {
  const first = useApi<Listing>("/datasets");
  const building = !!first.data?.items.some((n) => n.status === "building" || n.building);
  return useApi<Listing>("/datasets", { refetchMs: building ? 3000 : undefined });
}

/** A write about one project (activate, build, cancel, delete); null when the server never answered. */
export function projectWrite(id: string, action: "activate" | "build" | "cancel" | "delete"): Promise<Response | null> {
  const path = action === "delete" ? `/datasets/${id}` : `/datasets/${id}/${action}`;
  const method: WriteMethod = action === "delete" ? "DELETE" : "POST";
  return apiWrite(path, {}, method).catch(() => null);
}

/** Where a project's file is downloaded from. */
export function projectFileUrl(id: string): string {
  return `${API}/datasets/${id}/project.zip`;
}

/** The screen the project was left on (the opening screen the first time). */
export function lastScreen(): Screen {
  try {
    const s = localStorage.getItem("kap.screen");
    return s && (SCREENS as string[]).includes(s) ? (s as Screen) : OPENING;
  } catch {
    return OPENING;
  }
}

/**
 * After a switch was accepted: the server restarts on the other project's folder. Ask `/health`
 * until it answers from there, then load the workbench anew; no cache of the old project survives.
 * Returns a function that stops waiting.
 */
export function enterWhenBack(id: string, screen: Screen = OPENING, everyMs = 1000): () => void {
  const timer = window.setInterval(async () => {
    const r = await fetch(`${API}/health`).catch(() => null);
    const h = r?.ok ? await r.json().catch(() => null) : null;
    if (h?.dataset !== id) return;
    window.clearInterval(timer);
    touchRecent(id);
    window.location.hash = `#/${screen}`;
    window.location.reload();
  }, everyMs);
  return () => window.clearInterval(timer);
}
