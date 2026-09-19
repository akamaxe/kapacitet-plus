import { create } from "zustand";

export type Screen = "national" | "twin" | "compare" | "request" | "scenarios" | "method";
export const SCREENS: Screen[] = ["national", "twin", "compare", "request", "scenarios", "method"];
export type Scenario = "static" | "uncontrolled" | "doe";
export type Theme = "light" | "dark" | "system";

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

function screenFromHash(): Screen {
  const h = window.location.hash.replace("#/", "").replace("#", "");
  return (SCREENS as string[]).includes(h) ? (h as Screen) : "compare";
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

export const useStore = create<State>((set) => ({
  screen: screenFromHash(),
  lang: (readLS("kap.lang", "en") as "en" | "es"),
  theme: (readLS("kap.theme", "system") as Theme),
  notes: false,
  hour: DEFAULT_HOUR,
  hourTouched: false,
  scenario: "doe",
  playing: false,
  selectedCid: null,
  setScreen: (screen) => {
    window.location.hash = `#/${screen}`;
    set({ screen });
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
  setHour: (hour) => set({ hour, hourTouched: true }),
  setScenario: (scenario) => set({ scenario }),
  setPlaying: (playing) => set({ playing }),
  select: (selectedCid) => set({ selectedCid }),
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
  const s = screenFromHash();
  if (useStore.getState().screen !== s) useStore.setState({ screen: s });
});
