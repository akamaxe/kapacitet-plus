import { describe, expect, it, vi } from "vitest";
import { tFor } from "./i18n";
import { SCREEN_MENU, buildMenus, menuActions, type MenuActions, type MenuState } from "./menus";
import { SCREENS } from "./store";

// The menus are one list: the bar draws it, a narrow window shows it as a sheet and the search
// lists its actions. What is in it, and what is disabled when, is decided here.

const t = tFor("en");

function actions(): MenuActions {
  return {
    screen: vi.fn(), start: vi.fn(), newProject: vi.fn(), importFile: vi.fn(), exportProject: vi.fn(),
    properties: vi.fn(), print: vi.fn(), fullscreen: vi.fn(), theme: vi.fn(), lang: vi.fn(), computation: vi.fn(),
    search: vi.fn(), networkCheck: vi.fn(), settings: vi.fn(), keys: vi.fn(), tour: vi.fn(), manual: vi.fn(),
    about: vi.fn(),
  };
}

const work: MenuState = { view: "work", screen: "compare", ready: true, canProjects: true, theme: "light", lang: "en" };
const start: MenuState = { ...work, view: "start" };
const ids = (menus: ReturnType<typeof buildMenus>) => menus.flatMap((m) => m.items.map((i) => i.id));

describe("the menus of a project", () => {
  const menus = buildMenus(t, work, actions());

  it("are the six of an engineering program, in their order", () => {
    expect(menus.map((m) => m.label)).toEqual(["File", "View", "Study", "Results", "Tools", "Help"]);
  });

  it("hold every screen once, under its own name and with its own key", () => {
    const screens = menus.flatMap((m) => m.items.filter((i) => i.id.startsWith("screen.")).map((i) => ({ ...i, menu: m.id })));
    expect(screens.map((s) => s.id).sort()).toEqual(SCREENS.map((s) => `screen.${s}`).sort());
    for (const s of screens) {
      const name = s.id.slice("screen.".length) as (typeof SCREENS)[number];
      expect(s.label).toBe(t(`tab.${name}`));
      expect(s.keys).toBe(String(SCREENS.indexOf(name) + 1));
      expect(s.menu).toBe(SCREEN_MENU[name]);
    }
  });

  it("mark the screen on show, and only that one", () => {
    const current = menus.flatMap((m) => m.items).filter((i) => i.current);
    expect(current.map((i) => i.id)).toEqual(["screen.compare"]);
  });

  it("name every entry, once", () => {
    const all = ids(menus);
    expect(new Set(all).size).toBe(all.length);
    for (const item of menus.flatMap((m) => m.items)) {
      expect(item.label.trim(), item.id).not.toBe("");
      // a key the dictionary does not have comes back as the key itself
      expect(item.label, item.id).not.toMatch(/^(menu|tab|shell|start|ui)\./);
    }
  });

  it("check exactly one theme and one language", () => {
    const view = menus.find((m) => m.id === "view")!;
    const checked = (prefix: string) => view.items.filter((i) => i.id.startsWith(prefix) && i.checked).map((i) => i.id);
    expect(checked("theme.")).toEqual(["theme.light"]);
    expect(checked("lang.")).toEqual(["lang.en"]);
    expect(view.items.filter((i) => i.radio).every((i) => i.checked !== undefined)).toBe(true);
  });

  it("run what they say", () => {
    const a = actions();
    const m = buildMenus(t, work, a);
    const run = (id: string) => m.flatMap((x) => x.items).find((i) => i.id === id)!.run();
    run("screen.twin");
    expect(a.screen).toHaveBeenCalledWith("twin");
    run("theme.dark");
    expect(a.theme).toHaveBeenCalledWith("dark");
    run("lang.sv");
    expect(a.lang).toHaveBeenCalledWith("sv");
    run("file.close");
    run("file.open");
    expect(a.start).toHaveBeenCalledTimes(2);
    run("study.status");
    expect(a.computation).toHaveBeenCalledTimes(1);
  });
});

describe("the menus of the start window", () => {
  const menus = buildMenus(t, start, actions());

  it("are the four that act on projects and on the program", () => {
    expect(menus.map((m) => m.id)).toEqual(["file", "view", "tools", "help"]);
    expect(ids(menus).some((id) => id.startsWith("screen."))).toBe(false);
  });

  it("cannot print or close a project that is not on show", () => {
    const file = menus.find((m) => m.id === "file")!;
    const off = file.items.filter((i) => i.disabled).map((i) => i.id);
    expect(off).toEqual(["file.print", "file.close"]);
  });
});

describe("what a menu cannot do now", () => {
  it("is there, disabled: nothing computed, so no screen, no export and no tour", () => {
    const menus = buildMenus(t, { ...work, ready: false }, actions());
    const off = new Set(menus.flatMap((m) => m.items).filter((i) => i.disabled).map((i) => i.id));
    for (const s of SCREENS) expect(off.has(`screen.${s}`), s).toBe(true);
    for (const id of ["file.export", "file.print", "tools.check", "tools.settings", "help.tour"]) expect(off.has(id), id).toBe(true);
    // what does not need results stays
    for (const id of ["file.new", "file.open", "study.status", "tools.keys", "help.about"]) expect(off.has(id), id).toBe(false);
  });

  it("is there, disabled: a role that may not make projects", () => {
    const menus = buildMenus(t, { ...work, canProjects: false }, actions());
    const off = menus.flatMap((m) => m.items).filter((i) => i.disabled).map((i) => i.id);
    expect(off).toEqual(["file.new", "file.import"]);
  });
});

describe("the docks", () => {
  it("are listed only where the frame has them, with their state", () => {
    expect(ids(buildMenus(t, work, actions()))).not.toContain("view.tree");
    const a = { ...actions(), toggleTree: vi.fn(), toggleOutput: vi.fn() };
    const view = buildMenus(t, { ...work, treeOpen: true, outputOpen: false }, a).find((m) => m.id === "view")!;
    const tree = view.items.find((i) => i.id === "view.tree")!;
    const output = view.items.find((i) => i.id === "view.output")!;
    expect([tree.checked, tree.keys, output.checked, output.keys]).toEqual([true, "T", false, "O"]);
    // the tree belongs to a project: on the start window it is there and cannot be opened
    const onStart = buildMenus(t, { ...start, treeOpen: true, outputOpen: false }, a).find((m) => m.id === "view")!;
    expect(onStart.items.find((i) => i.id === "view.tree")!.disabled).toBe(true);
    expect(onStart.items.find((i) => i.id === "view.output")!.disabled).toBeFalsy();
  });
});

describe("the actions the search lists", () => {
  it("are the menus' own, without the screens, the choices and what is disabled", () => {
    const found = menuActions(buildMenus(t, work, actions()));
    const got = found.map((f) => f.id);
    expect(got).toContain("file.new");
    expect(got).toContain("study.status");
    expect(got).toContain("help.about");
    expect(got.some((id) => id.startsWith("screen.") || id.startsWith("theme.") || id.startsWith("lang."))).toBe(false);
    // the search does not list itself, and the two ways to the start window are one
    expect(got).not.toContain("tools.search");
    expect(got.filter((id) => id === "file.open" || id === "file.close")).toEqual(["file.open"]);
    expect(found.find((f) => f.id === "file.new")!.menu).toBe("File");
    const none = menuActions(buildMenus(t, { ...start, ready: false, canProjects: false }, actions())).map((f) => f.id);
    expect(none).not.toContain("file.new");
    expect(none).not.toContain("help.tour");
  });
});
