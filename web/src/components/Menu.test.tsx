import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Menu } from "../lib/menus";
import { MenuBar, menuOpen, roleOf, sections } from "./Menu";

// The menu bar behaves as a desktop program's: Down opens a menu, Left and Right walk the menus,
// Up and Down the entries, a letter jumps, Enter does it and Escape hands the focus back.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ran: string[] = [];
const item = (id: string, label: string, more: Partial<Menu["items"][number]> = {}) => ({ id, label, run: () => { ran.push(id); }, ...more });
const MENUS: Menu[] = [
  { id: "file", label: "File", items: [item("new", "New project…"), item("open", "Open project…", { keys: "0" }), item("print", "Print…", { disabled: true, sep: true }), item("close", "Close project", { sep: true })] },
  { id: "view", label: "View", items: [item("light", "Light", { checked: true, radio: true, group: "Theme" }), item("dark", "Dark", { checked: false, radio: true }), item("tree", "Project tree", { checked: false, sep: true })] },
  { id: "help", label: "Help", items: [item("about", "About…")] },
];

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(menus: Menu[] = MENUS): Promise<HTMLElement> {
  host = document.createElement("div");
  document.body.appendChild(host);
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<MenuBar menus={menus} label="Menus" />);
  });
  return host;
}

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  ran.length = 0;
});

const tops = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLButtonElement>(".menu-top"));
const pop = (el: HTMLElement) => el.querySelector<HTMLElement>('[role="menu"]');
const key = async (target: Element | null, k: string) => {
  await act(async () => {
    target?.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
  });
};
const click = async (target: Element | null, detail = 1) => {
  await act(async () => {
    target?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail }));
  });
};
const focusedLabel = () => (document.activeElement as HTMLElement | null)?.dataset.label ?? document.activeElement?.textContent ?? "";

describe("the pieces", () => {
  it("part a menu's entries at each rule and each group's name", () => {
    const parts = sections(MENUS[0].items);
    expect(parts.map((p) => p.items.map((i) => i.id))).toEqual([["new", "open"], ["print"], ["close"]]);
    expect(sections(MENUS[1].items).map((p) => p.group)).toEqual(["Theme", undefined]);
  });

  it("say what kind of entry each is", () => {
    expect(roleOf(MENUS[0].items[0])).toBe("menuitem");
    expect(roleOf(MENUS[1].items[0])).toBe("menuitemradio");
    expect(roleOf(MENUS[1].items[2])).toBe("menuitemcheckbox");
  });
});

describe("the menu bar", () => {
  it("is a menubar of named menus, one of them in the tab order", async () => {
    const el = await render();
    expect(el.querySelector('[role="menubar"]')?.getAttribute("aria-label")).toBe("Menus");
    expect(tops(el).map((b) => [b.textContent, b.getAttribute("aria-haspopup"), b.getAttribute("aria-expanded"), b.tabIndex]))
      .toEqual([["File", "menu", "false", 0], ["View", "menu", "false", -1], ["Help", "menu", "false", -1]]);
    expect(pop(el)).toBeNull();
    expect(menuOpen()).toBe(false);
  });

  it("opens under the pointer without taking the focus into the menu, and closes on a second click", async () => {
    const el = await render();
    tops(el)[0].focus();
    await click(tops(el)[0]);
    expect(pop(el)?.getAttribute("aria-label")).toBe("File");
    expect(tops(el)[0].getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(tops(el)[0]);
    expect(menuOpen()).toBe(true);
    await click(tops(el)[0]);
    expect(pop(el)).toBeNull();
    expect(menuOpen()).toBe(false);
  });

  it("opens from the keyboard on its first entry, or on its last with Up", async () => {
    const el = await render();
    tops(el)[0].focus();
    await key(tops(el)[0], "ArrowDown");
    expect(focusedLabel()).toBe("New project…");
    await key(document.activeElement, "Escape");
    expect(pop(el)).toBeNull();
    expect(document.activeElement).toBe(tops(el)[0]);
    await key(tops(el)[0], "ArrowUp");
    expect(focusedLabel()).toBe("Close project");
    await key(document.activeElement, "Escape");
    // Enter and Space reach a button as a click with no pointer behind it
    await click(tops(el)[0], 0);
    expect(focusedLabel()).toBe("New project…");
  });

  it("walks the entries, past the one that is disabled, and round the ends", async () => {
    const el = await render();
    tops(el)[0].focus();
    await key(tops(el)[0], "ArrowDown");
    await key(document.activeElement, "ArrowDown");
    expect(focusedLabel()).toBe("Open project…");
    await key(document.activeElement, "ArrowDown");
    expect(focusedLabel()).toBe("Close project");
    await key(document.activeElement, "ArrowDown");
    expect(focusedLabel()).toBe("New project…");
    await key(document.activeElement, "ArrowUp");
    expect(focusedLabel()).toBe("Close project");
    await key(document.activeElement, "Home");
    expect(focusedLabel()).toBe("New project…");
    await key(document.activeElement, "End");
    expect(focusedLabel()).toBe("Close project");
    const print = el.querySelector<HTMLElement>('[data-label="Print…"]')!;
    expect(print.getAttribute("aria-disabled")).toBe("true");
  });

  it("jumps to the entry a letter starts, the next one each time", async () => {
    const el = await render();
    tops(el)[0].focus();
    await key(tops(el)[0], "ArrowDown");
    await key(document.activeElement, "c");
    expect(focusedLabel()).toBe("Close project");
    await key(document.activeElement, "o");
    expect(focusedLabel()).toBe("Open project…");
    await key(document.activeElement, "x");
    expect(focusedLabel()).toBe("Open project…");
  });

  it("walks the menus with Left and Right, open or closed", async () => {
    const el = await render();
    tops(el)[0].focus();
    await key(tops(el)[0], "ArrowRight");
    expect(document.activeElement).toBe(tops(el)[1]);
    expect(pop(el)).toBeNull();
    expect(tops(el).map((b) => b.tabIndex)).toEqual([-1, 0, -1]);
    await key(tops(el)[1], "ArrowDown");
    expect(pop(el)?.getAttribute("aria-label")).toBe("View");
    await key(document.activeElement, "ArrowRight");
    expect(pop(el)?.getAttribute("aria-label")).toBe("Help");
    expect(focusedLabel()).toBe("About…");
    await key(document.activeElement, "ArrowRight");
    expect(pop(el)?.getAttribute("aria-label")).toBe("File");
    await key(document.activeElement, "ArrowLeft");
    expect(pop(el)?.getAttribute("aria-label")).toBe("Help");
  });

  it("does an entry, closes, and leaves the focus on the menu's name", async () => {
    const el = await render();
    tops(el)[0].focus();
    await key(tops(el)[0], "ArrowDown");
    await click(document.activeElement, 0);
    expect(ran).toEqual(["new"]);
    expect(pop(el)).toBeNull();
    expect(document.activeElement).toBe(tops(el)[0]);
  });

  it("does nothing for an entry that is disabled, and stays open", async () => {
    const el = await render();
    await click(tops(el)[0]);
    await click(el.querySelector('[data-label="Print…"]'));
    expect(ran).toEqual([]);
    expect(pop(el)).not.toBeNull();
  });

  it("says which choice is taken", async () => {
    const el = await render();
    await click(tops(el)[1]);
    const light = el.querySelector('[data-label="Light"]')!;
    const dark = el.querySelector('[data-label="Dark"]')!;
    const tree = el.querySelector('[data-label="Project tree"]')!;
    expect([light.getAttribute("role"), light.getAttribute("aria-checked")]).toEqual(["menuitemradio", "true"]);
    expect([dark.getAttribute("role"), dark.getAttribute("aria-checked")]).toEqual(["menuitemradio", "false"]);
    expect([tree.getAttribute("role"), tree.getAttribute("aria-checked")]).toEqual(["menuitemcheckbox", "false"]);
    expect(el.querySelector('[role="group"]')?.getAttribute("aria-label")).toBe("Theme");
  });

  it("switches menus under the pointer only once one is open", async () => {
    const el = await render();
    const over = async (target: Element) => {
      await act(async () => {
        target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body }));
      });
    };
    await over(tops(el)[1]);
    expect(pop(el)).toBeNull();
    await click(tops(el)[0]);
    await over(tops(el)[1]);
    expect(pop(el)?.getAttribute("aria-label")).toBe("View");
  });

  it("closes at a press outside it", async () => {
    const el = await render();
    await click(tops(el)[0]);
    expect(pop(el)).not.toBeNull();
    await act(async () => {
      // jsdom has no PointerEvent: the listener only reads the event's target
      const press = new Event("pointerdown", { bubbles: true });
      document.body.dispatchEvent(press);
    });
    expect(pop(el)).toBeNull();
  });

  it("closes when the menus themselves change, as between the start window and a project", async () => {
    const el = await render();
    tops(el)[2].focus();
    await key(tops(el)[2], "ArrowDown");
    expect(pop(el)).not.toBeNull();
    const spy = vi.fn();
    await act(async () => {
      root!.render(<MenuBar menus={MENUS.slice(0, 2)} label="Menus" />);
      spy();
    });
    expect(pop(el)).toBeNull();
    expect(tops(el).filter((b) => b.tabIndex === 0)).toHaveLength(1);
  });
});
