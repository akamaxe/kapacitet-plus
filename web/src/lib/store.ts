import { create } from "zustand";
import { isLang, loadLang, type Lang } from "./i18n/core";

export type Screen = "national" | "twin" | "compare" | "request" | "operation" | "report" | "scenarios" | "method";
export const SCREENS: Screen[] = ["national", "twin", "compare", "request", "operation", "report", "scenarios", "method"];
export type Scenario = "static" | "uncontrolled" | "doe";
/** The envelope a screen reads: perfect foresight (the planning bound) or issued the day before. */
export type Basis = "foresight" | "dayahead";
export const BASES: Basis[] = ["foresight", "dayahead"];
/** The sharing rules of engine/doe.py; a screen follows the DSO's setting unless a link names one. */
export const POLICIES = ["maxmin", "prorata", "propfair", "maxtotal"] as const;
export type Theme = "light" | "dark" | "system";
const THEMES: readonly string[] = ["light", "dark", "system"];

function readLS(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeLS(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: ignore */
  }
}

/**
 * `#/<screen>?hour=…&scenario=…&cid=…` — what someone is looking at, so a view can be
 * sent to a colleague and a figure in a report can be traced back to it.
 *
 * Only things that change what the numbers mean travel: the hour, the scenario, the selected
 * house and, on the request screen, the saved assessment being read (`job`). A link that
 * autoplays, or that pre-fills a request form with an answer nobody computed, would be worse
 * than no link at all; a `job` is an answer the engine did compute, kept on disk.
 */
export const SCENARIOS: Scenario[] = ["static", "uncontrolled", "doe"];

export interface UrlView {
  screen: Screen;
  /** a screen name in the link that does not exist (the view falls back to the opening screen) */
  notFound?: string;
  hour?: number;
  scenario?: Scenario;
  cid?: number;
  job?: string;
  basis?: Basis;
  policy?: string;
  /** the day the operation screen shows, as YYYY-MM-DD */
  date?: string;
  /** the network the link was made on (an imported one; the reference network writes none) */
  ds?: string;
}

/** A saved assessment's id (data/requests/<id>.json.gz). */
export const JOB_ID = /^[0-9a-f]{12}$/;
/** A calendar date as the date input and the API write it. */
export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** What a link asks to look at. Pure: the address is an argument, so it can be tested. */
export function parseHash(hash: string = window.location.hash): UrlView {
  const raw = hash.replace(/^#\/?/, "");
  const [path, search] = raw.split("?");
  const known = (SCREENS as string[]).includes(path);
  const screen = known ? (path as Screen) : "compare";
  const q = new URLSearchParams(search ?? "");
  const out: UrlView = { screen, ...(path && !known ? { notFound: path } : {}) };
  // Number(null) is 0, so every one of these needs the presence check first: without it
  // a link with no hour reads as hour 0 and resets the view on every tab click.
  const num = (key: string) => {
    const raw = q.get(key);
    if (raw === null || raw.trim() === "") return undefined;
    const n = Number(raw);
    return Number.isInteger(n) ? n : undefined;
  };
  const hour = num("hour");
  if (hour !== undefined && hour >= 0 && hour < 8784) out.hour = hour;
  const scenario = q.get("scenario");
  if (scenario && (SCENARIOS as string[]).includes(scenario)) out.scenario = scenario as Scenario;
  const cid = num("cid");
  if (cid !== undefined && cid >= 0) out.cid = cid;
  const job = q.get("job");
  if (job && JOB_ID.test(job)) out.job = job;
  const basis = q.get("basis");
  if (basis && (BASES as string[]).includes(basis)) out.basis = basis as Basis;
  const policy = q.get("policy");
  if (policy && (POLICIES as readonly string[]).includes(policy)) out.policy = policy;
  const date = q.get("date");
  if (date && ISO_DATE.test(date) && !Number.isNaN(Date.parse(date))) out.date = date;
  const ds = q.get("ds");
  if (ds && /^[a-z0-9][a-z0-9-]{2,40}$/.test(ds)) out.ds = ds;
  return out;
}

/** The last hash this module wrote, so our own replaceState does not look like a visit. */
let written = "";

/** The parts of the view a link carries. */
export type LinkedView = Pick<State, "screen" | "hourTouched" | "hour" | "scenario" | "selectedCid" | "job"> &
  Partial<Pick<State, "basis" | "policy" | "opDate" | "dataset">>;

/** The basis a report opens on: what is issued the day before. Perfect foresight is the planning
 *  bound, which nobody can issue, so it is the one a link has to ask for. */
export const DEFAULT_BASIS: Basis = "dayahead";

/** The address for what is on screen. Pure, like parseHash. */
export function hashFor(st: LinkedView): string {
  const q = new URLSearchParams();
  // omit anything at its default, and anything this screen does not read: an hour on the
  // method appendix is noise in a link somebody is meant to paste somewhere
  const usesHour = st.screen === "twin" || st.screen === "request";
  if (usesHour && st.hourTouched) q.set("hour", String(st.hour));
  if (st.screen === "twin" && st.scenario !== "doe") q.set("scenario", st.scenario);
  const usesHouse = usesHour || st.screen === "report" || st.screen === "operation";
  if (usesHouse && st.selectedCid !== null) q.set("cid", String(st.selectedCid));
  if (st.screen === "operation" && st.opDate) q.set("date", st.opDate);
  if (st.screen === "request" && st.job) q.set("job", st.job);
  // the screens that read a basis and a rule carry them: the basis away from its default, the rule
  // whenever one was chosen over the DSO's setting (which can change under a link)
  const readsCombo = st.screen === "report";
  if (readsCombo && st.basis && st.basis !== DEFAULT_BASIS) q.set("basis", st.basis);
  if (readsCombo && st.policy) q.set("policy", st.policy);
  // a view of an imported network names it, so the link cannot be read on the reference network
  if (st.dataset && st.dataset !== "reference") q.set("ds", st.dataset);
  const qs = q.toString();
  return `#/${st.screen}${qs ? `?${qs}` : ""}`;
}

function writeHash(push: boolean) {
  const hash = hashFor(useStore.getState());
  if (hash === written) return;
  written = hash;
  // replaceState, not location.hash: the hour changes up to eight times a second during
  // playback and once per drag frame on the year strip, and each one would otherwise be
  // a history entry, which destroys the back button.
  history[push ? "pushState" : "replaceState"](null, "", hash);
}

/** Write what is on screen into the address bar without a history entry (a link that fell back). */
export function syncUrl() {
  written = "";
  writeHash(false);
}

let hourTimer: number | undefined;

function scheduleHashWrite() {
  if (useStore.getState().playing) return; // written once when playback stops
  window.clearTimeout(hourTimer);
  hourTimer = window.setTimeout(() => writeHash(false), 250);
}

let dateTimer: number | undefined;

/** A date input steps a day per arrow key: one replaceState once it settles, not one per day. */
function scheduleDateWrite() {
  window.clearTimeout(dateTimer);
  dateTimer = window.setTimeout(() => writeHash(false), 250);
}

interface State {
  screen: Screen;
  lang: Lang;
  theme: Theme;
  hour: number;
  /** true once the viewer has moved the hour; until then the twin opens on the showcase hour from /meta */
  hourTouched: boolean;
  scenario: Scenario;
  playing: boolean;
  selectedCid: number | null;
  /** the saved assessment shown on the request screen, if any */
  job: string | null;
  /** the envelope the report reads, and the rule it reads it under (null: the DSO's setting) */
  basis: Basis;
  policy: string | null;
  /** the day the operation screen shows (null: the season's busiest day, which it works out) */
  opDate: string | null;
  /** the network the server is on (from /health), and the one a link named, if any */
  dataset: string | null;
  linkDataset: string | null;
  /** the tour's step, or null when it is closed; `tourInstant` when opened from the keyboard */
  tour: number | null;
  tourInstant: boolean;
  /** a first visit is offered the tour once (Tour.tsx decides whether it was seen) */
  tourOffered: boolean;
  /** a link named a screen that does not exist; App says so once */
  notFound: string | null;
  /** the shortcuts that are one character (the screen numbers, F, /, ?), which a speech-input user may need off */
  singleKeys: boolean;
  setSingleKeys: (on: boolean) => void;
  setTour: (step: number | null, instant?: boolean) => void;
  setScreen: (s: Screen) => void;
  /** switches once the language's dictionary has loaded, so nothing shows in a half-loaded language */
  setLang: (l: Lang) => Promise<void>;
  setTheme: (t: Theme) => void;
  setHour: (h: number) => void;
  setScenario: (s: Scenario) => void;
  setPlaying: (p: boolean) => void;
  select: (cid: number | null) => void;
  setJob: (job: string | null) => void;
  setBasis: (basis: Basis) => void;
  setPolicy: (policy: string | null) => void;
  setOpDate: (date: string | null) => void;
}

// Fallback hour (12 July, 12:00 local standard time) until /meta provides the showcase hour.
const DEFAULT_HOUR = (31 + 28 + 31 + 30 + 31 + 30 + 11) * 24 + 12;

const initial = parseHash();

export const useStore = create<State>((set) => ({
  screen: initial.screen,
  // an unknown stored language would leave every lookup empty, so it falls back to English
  lang: ((v) => (isLang(v) ? v : "en"))(readLS("kap.lang", "en")),
  // day is the default (DESIGN.md); a value this version does not know falls back to it
  theme: ((t) => (THEMES.includes(t) ? t : "light") as Theme)(readLS("kap.theme", "light")),
  hour: initial.hour ?? DEFAULT_HOUR,
  hourTouched: initial.hour !== undefined,
  scenario: initial.scenario ?? "doe",
  playing: false,
  selectedCid: initial.cid ?? null,
  job: initial.job ?? null,
  basis: initial.basis ?? DEFAULT_BASIS,
  policy: initial.policy ?? null,
  opDate: initial.date ?? null,
  dataset: null,
  linkDataset: initial.ds ?? null,
  tour: null,
  tourInstant: false,
  tourOffered: false,
  notFound: initial.notFound ?? null,
  singleKeys: readLS("kap.keys", "on") !== "off",
  setSingleKeys: (on) => {
    writeLS("kap.keys", on ? "on" : "off");
    set({ singleKeys: on });
  },
  setTour: (tour, instant = false) => set((s) => ({ tour, tourInstant: tour === null ? false : s.tour === null ? instant : s.tourInstant, tourOffered: false })),
  setScreen: (screen) => {
    set({ screen });
    writeHash(true); // a screen is a place, so it gets a history entry
  },
  setLang: async (lang) => {
    try {
      await loadLang(lang);
    } catch {
      return; // offline and not loaded yet: stay in the language that is on screen
    }
    writeLS("kap.lang", lang);
    document.documentElement.lang = lang;
    set({ lang });
  },
  setTheme: (theme) => {
    writeLS("kap.theme", theme);
    applyTheme(theme);
    set({ theme });
  },
  setHour: (hour) => {
    set({ hour, hourTouched: true });
    scheduleHashWrite();
  },
  setScenario: (scenario) => {
    set({ scenario });
    writeHash(false);
  },
  setPlaying: (playing) => {
    // the scrubber stops playback on every input; only a real change may touch the URL,
    // or it would call replaceState at pointer rate past the 250 ms debounce
    if (playing === useStore.getState().playing) return;
    set({ playing });
    if (!playing) writeHash(false); // catch up with where playback left the hour
  },
  select: (selectedCid) => {
    // an assessment belongs to its house: choosing another house leaves it behind
    set((s) => ({ selectedCid, job: selectedCid === s.selectedCid ? s.job : null }));
    writeHash(false);
  },
  setJob: (job) => {
    set({ job });
    writeHash(false);
  },
  setBasis: (basis) => {
    set({ basis });
    writeHash(false);
  },
  setPolicy: (policy) => {
    set({ policy });
    writeHash(false);
  },
  setOpDate: (opDate) => {
    set({ opDate });
    scheduleDateWrite();
  },
}));

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function isDark(): boolean {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr) return attr === "dark";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

let syncing = false;

/**
 * Follow the address bar: a link opened, the back button, a hand-edited hash. Only the application
 * calls this (App.tsx); the applicant's portal shares the store for its language, and a listener
 * registered on import would turn its `#/apply` into the opening screen.
 */
export function initUrlSync(): void {
  if (syncing) return;
  syncing = true;
  window.addEventListener("hashchange", onHashChange);
}

function onHashChange() {
  if (window.location.hash === written) return; // our own write, echoed back
  const v = parseHash();
  written = window.location.hash;
  useStore.setState({
    screen: v.screen,
    ...(v.hour !== undefined ? { hour: v.hour, hourTouched: true } : {}),
    ...(v.scenario ? { scenario: v.scenario } : {}),
    // a link without a parameter means "this screen", not "forget what I had selected"
    ...(v.cid !== undefined ? { selectedCid: v.cid } : {}),
    ...(v.job !== undefined ? { job: v.job } : {}),
    ...(v.basis !== undefined ? { basis: v.basis } : {}),
    ...(v.policy !== undefined ? { policy: v.policy } : {}),
    ...(v.date !== undefined ? { opDate: v.date } : {}),
    ...(v.notFound ? { notFound: v.notFound } : {}),
    playing: false,
  });
  // A tab link carries only the screen, but the view still has an hour and a selection.
  // Put them back in the address bar so what is on screen is always what the link says.
  writeHash(false);
}

/** Each screen owns its own scroller above 1100 px, so there is no single position. */
export const scrollPositions = new Map<Screen, number>();
