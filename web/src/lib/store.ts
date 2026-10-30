import { create } from "zustand";

export type Screen = "national" | "twin" | "compare" | "request" | "scenarios" | "method";
export const SCREENS: Screen[] = ["national", "twin", "compare", "request", "scenarios", "method"];
export type Scenario = "static" | "uncontrolled" | "doe";
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
 * Only things that change what the numbers mean travel: the hour, the scenario and the
 * selected house. A link that autoplays, or that pre-fills a request form with an answer
 * nobody computed, would be worse than no link at all.
 */
export const SCENARIOS: Scenario[] = ["static", "uncontrolled", "doe"];

export interface UrlView {
  screen: Screen;
  hour?: number;
  scenario?: Scenario;
  cid?: number;
}

function parseHash(): UrlView {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, search] = raw.split("?");
  const screen = (SCREENS as string[]).includes(path) ? (path as Screen) : "compare";
  const q = new URLSearchParams(search ?? "");
  const out: UrlView = { screen };
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
  return out;
}

/** The last hash this module wrote, so our own replaceState does not look like a visit. */
let written = "";

function writeHash(push: boolean) {
  const st = useStore.getState();
  const q = new URLSearchParams();
  // omit anything at its default, and anything this screen does not read: an hour on the
  // method appendix is noise in a link somebody is meant to paste somewhere
  const usesHour = st.screen === "twin" || st.screen === "request";
  if (usesHour && st.hourTouched) q.set("hour", String(st.hour));
  if (st.screen === "twin" && st.scenario !== "doe") q.set("scenario", st.scenario);
  if (usesHour && st.selectedCid !== null) q.set("cid", String(st.selectedCid));
  const qs = q.toString();
  const hash = `#/${st.screen}${qs ? `?${qs}` : ""}`;
  if (hash === written) return;
  written = hash;
  // replaceState, not location.hash: the hour changes up to eight times a second during
  // playback and once per drag frame on the year strip, and each one would otherwise be
  // a history entry, which destroys the back button.
  history[push ? "pushState" : "replaceState"](null, "", hash);
}

let hourTimer: number | undefined;

function scheduleHashWrite() {
  if (useStore.getState().playing) return; // written once when playback stops
  window.clearTimeout(hourTimer);
  hourTimer = window.setTimeout(() => writeHash(false), 250);
}

interface State {
  screen: Screen;
  lang: "en" | "es";
  theme: Theme;
  notes: boolean;
  hour: number;
  /** true once the viewer has moved the hour; until then the twin opens on the showcase hour from /meta */
  hourTouched: boolean;
  scenario: Scenario;
  playing: boolean;
  selectedCid: number | null;
  setScreen: (s: Screen) => void;
  setLang: (l: "en" | "es") => void;
  setTheme: (t: Theme) => void;
  toggleNotes: () => void;
  setHour: (h: number) => void;
  setScenario: (s: Scenario) => void;
  setPlaying: (p: boolean) => void;
  select: (cid: number | null) => void;
}

// Fallback hour (12 July, 12:00 local standard time) until /meta provides the showcase hour.
const DEFAULT_HOUR = (31 + 28 + 31 + 30 + 31 + 30 + 11) * 24 + 12;

const initial = parseHash();

export const useStore = create<State>((set) => ({
  screen: initial.screen,
  // an unknown stored language would leave every lookup empty, so it falls back to English
  lang: (readLS("kap.lang", "en") === "es" ? "es" : "en"),
  // day is the default (DESIGN.md); a value this version does not know falls back to it
  theme: ((t) => (THEMES.includes(t) ? t : "light") as Theme)(readLS("kap.theme", "light")),
  notes: false,
  hour: initial.hour ?? DEFAULT_HOUR,
  hourTouched: initial.hour !== undefined,
  scenario: initial.scenario ?? "doe",
  playing: false,
  selectedCid: initial.cid ?? null,
  setScreen: (screen) => {
    set({ screen });
    writeHash(true); // a screen is a place, so it gets a history entry
  },
  setLang: (lang) => {
    writeLS("kap.lang", lang);
    document.documentElement.lang = lang;
    set({ lang });
  },
  setTheme: (theme) => {
    writeLS("kap.theme", theme);
    applyTheme(theme);
    set({ theme });
  },
  toggleNotes: () => set((s) => ({ notes: !s.notes })),
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
    set({ selectedCid });
    writeHash(false);
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

window.addEventListener("hashchange", () => {
  if (window.location.hash === written) return; // our own write, echoed back
  const v = parseHash();
  written = window.location.hash;
  useStore.setState({
    screen: v.screen,
    ...(v.hour !== undefined ? { hour: v.hour, hourTouched: true } : {}),
    ...(v.scenario ? { scenario: v.scenario } : {}),
    // a link without a parameter means "this screen", not "forget what I had selected"
    ...(v.cid !== undefined ? { selectedCid: v.cid } : {}),
    playing: false,
  });
  // A tab link carries only the screen, but the view still has an hour and a selection.
  // Put them back in the address bar so what is on screen is always what the link says.
  writeHash(false);
});

/** Each screen owns its own scroller above 1100 px, so there is no single position. */
export const scrollPositions = new Map<Screen, number>();
