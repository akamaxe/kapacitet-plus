import { LANGS, LANG_NAMES, type Lang, type TFn } from "./i18n";
import { SCREENS, type Screen, type Theme, type View } from "./store";

/**
 * The menus, as data. One list feeds the menu bar, the sheet a narrow window shows in its place
 * and the actions the search finds, so the three cannot drift apart. Only what exists is listed:
 * an entry is something the program does, and one it cannot do now is there but disabled.
 */
export interface MenuItem {
  id: string;
  label: string;
  /** the keys that do the same, as the key sheet writes them */
  keys?: string;
  run: () => void;
  disabled?: boolean;
  /** a choice or a switch: its state. A plain action has none. */
  checked?: boolean;
  /** one of a group of which exactly one is checked */
  radio?: boolean;
  /** the screen on show */
  current?: boolean;
  /** a rule above this entry */
  sep?: boolean;
  /** the name of the group this entry begins, said above it */
  group?: string;
}

export interface Menu {
  id: "file" | "view" | "study" | "results" | "tools" | "help";
  label: string;
  items: MenuItem[];
}

/** What the menus depend on. */
export interface MenuState {
  view: View;
  screen: Screen;
  /** the project in use has results, so its screens have something to show */
  ready: boolean;
  /** the signed-in role may make, open and change projects */
  canProjects: boolean;
  theme: Theme;
  lang: Lang;
  /** the docks of the workbench; undefined where the frame has none */
  treeOpen?: boolean;
  outputOpen?: boolean;
}

/** What the menus do. The frame supplies these; the list only says which is where. */
export interface MenuActions {
  screen: (s: Screen) => void;
  start: () => void;
  newProject: () => void;
  importFile: () => void;
  exportProject: () => void;
  properties: () => void;
  print: () => void;
  toggleTree?: () => void;
  toggleOutput?: () => void;
  fullscreen: () => void;
  theme: (t: Theme) => void;
  lang: (l: Lang) => void;
  computation: () => void;
  search: () => void;
  networkCheck: () => void;
  settings: () => void;
  keys: () => void;
  tour: () => void;
  manual: () => void;
  about: () => void;
}

/** Which menu each screen is found in: looking at the network, studying it, reading results. */
export const SCREEN_MENU: Record<Screen, "view" | "study" | "results"> = {
  national: "view",
  twin: "view",
  compare: "study",
  request: "study",
  operation: "study",
  scenarios: "study",
  report: "results",
  method: "results",
};

const THEMES: Theme[] = ["light", "dark", "system"];

export function buildMenus(t: TFn, s: MenuState, a: MenuActions): Menu[] {
  const work = s.view === "work";
  // a screen needs results to show
  const screens = (menu: "view" | "study" | "results"): MenuItem[] =>
    SCREENS.filter((sc) => SCREEN_MENU[sc] === menu).map((sc) => ({
      id: `screen.${sc}`,
      label: t(`tab.${sc}`),
      keys: String(SCREENS.indexOf(sc) + 1),
      run: () => a.screen(sc),
      disabled: !s.ready,
      current: work && s.screen === sc,
    }));

  const file: Menu = {
    id: "file",
    label: t("menu.file"),
    items: [
      { id: "file.new", label: t("start.new"), run: a.newProject, disabled: !s.canProjects },
      { id: "file.open", label: t("menu.file.open"), keys: "0", run: a.start },
      { id: "file.import", label: t("menu.file.import"), run: a.importFile, disabled: !s.canProjects },
      { id: "file.export", label: t("menu.file.export"), run: a.exportProject, disabled: !s.ready, sep: true },
      { id: "file.properties", label: t("menu.file.properties"), run: a.properties },
      { id: "file.print", label: t("menu.file.print"), keys: "Ctrl P", run: a.print, disabled: !work || !s.ready, sep: true },
      { id: "file.close", label: t("menu.file.close"), run: a.start, disabled: !work, sep: true },
    ],
  };

  const docks: MenuItem[] = [];
  if (a.toggleTree) docks.push({ id: "view.tree", label: t("menu.view.tree"), keys: "T", run: a.toggleTree, checked: !!s.treeOpen, disabled: !work });
  if (a.toggleOutput) docks.push({ id: "view.output", label: t("menu.view.output"), keys: "O", run: a.toggleOutput, checked: !!s.outputOpen });
  // the two screens that look at the network; the start window has no study on show
  const looks = work ? screens("view") : [];
  const frame: MenuItem[] = [...docks, { id: "view.fullscreen", label: t("shell.fullscreen"), keys: "F", run: a.fullscreen }];
  if (looks.length && frame.length) frame[0] = { ...frame[0], sep: true };
  const view: Menu = {
    id: "view",
    label: t("menu.view"),
    items: [
      ...looks,
      ...frame,
      ...THEMES.map((th, i): MenuItem => ({
        id: `theme.${th}`,
        label: t(`shell.theme.${th}`),
        run: () => a.theme(th),
        checked: s.theme === th,
        radio: true,
        ...(i === 0 ? { sep: true, group: t("ui.theme") } : {}),
      })),
      ...LANGS.map((l, i): MenuItem => ({
        id: `lang.${l}`,
        label: LANG_NAMES[l],
        run: () => a.lang(l),
        checked: s.lang === l,
        radio: true,
        ...(i === 0 ? { sep: true, group: t("shell.language") } : {}),
      })),
    ],
  };

  const study: Menu = {
    id: "study",
    label: t("menu.study"),
    items: [...screens("study"), { id: "study.status", label: t("menu.study.status"), run: a.computation, sep: true }],
  };

  const results: Menu = { id: "results", label: t("menu.results"), items: screens("results") };

  const tools: Menu = {
    id: "tools",
    label: t("menu.tools"),
    items: [
      { id: "tools.search", label: t("menu.tools.search"), keys: "Ctrl K", run: a.search },
      { id: "tools.check", label: t("menu.tools.check"), run: a.networkCheck, disabled: !s.ready, sep: true },
      { id: "tools.settings", label: t("menu.tools.settings"), run: a.settings, disabled: !s.ready },
      { id: "tools.keys", label: t("menu.tools.keys"), keys: "?", run: a.keys, sep: true },
    ],
  };

  const help: Menu = {
    id: "help",
    label: t("menu.help"),
    items: [
      { id: "help.tour", label: t("menu.help.tour"), run: a.tour, disabled: !s.ready },
      { id: "help.manual", label: t("start.manual"), run: a.manual },
      { id: "help.about", label: t("menu.help.about"), run: a.about, sep: true },
    ],
  };

  // the start window has no study on show: its menus are the ones that act on projects and on
  // the program. Inside a project the studies and their results have their own.
  return work ? [file, view, study, results, tools, help] : [file, view, tools, help];
}

/** Every entry that is an action and not a screen, a theme or a language: what the search lists. */
export function menuActions(menus: Menu[]): (MenuItem & { menu: string })[] {
  const seen = new Set<string>();
  const out: (MenuItem & { menu: string })[] = [];
  for (const menu of menus) {
    for (const item of menu.items) {
      if (item.id.startsWith("screen.") || item.radio || item.disabled || item.id === "tools.search") continue;
      // "Open project…" and "Close project" both lead to the start window: one entry is enough
      const key = item.id === "file.close" ? "file.open" : item.id;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ ...item, menu: menu.label });
    }
  }
  return out;
}
