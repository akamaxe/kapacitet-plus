/**
 * The menu bar: the program's commands under six names, as engineering software lays them out.
 *
 * It follows the menubar pattern a desktop program has: a click or the Down key opens a menu,
 * Left and Right walk the menus, Up and Down the entries, a letter jumps to the entry that
 * starts with it, Enter does it and Escape closes and hands the focus back to the menu's name.
 * Hovering switches menus only once one is open. Nothing animates: a menu is opened many times
 * a day, most of them from the keyboard.
 *
 * The entries come from lib/menus.ts; this file only draws them and moves the focus.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { Menu, MenuItem } from "../lib/menus";
import { Icon } from "./Icon";

let openCount = 0;

/** A menu is open: the keys belong to it, so the frame's shortcuts stay quiet as under a dialog. */
export function menuOpen(): boolean {
  return openCount > 0;
}

/** The entries of a menu in sections: a rule or a group's name starts a new one. */
export function sections(items: MenuItem[]): { group?: string; items: MenuItem[] }[] {
  const out: { group?: string; items: MenuItem[] }[] = [];
  for (const item of items) {
    if (!out.length || item.sep || item.group) out.push({ group: item.group, items: [] });
    out[out.length - 1].items.push(item);
  }
  return out;
}

export function roleOf(item: MenuItem): "menuitem" | "menuitemradio" | "menuitemcheckbox" {
  if (item.checked === undefined) return "menuitem";
  return item.radio ? "menuitemradio" : "menuitemcheckbox";
}

export function MenuBar({ menus, label }: { menus: Menu[]; label: string }) {
  const [open, setOpen] = useState<number | null>(null);
  // the one name in the tab order; the arrows move it
  const [tab, setTab] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const tops = useRef<(HTMLButtonElement | null)[]>([]);
  // where the focus lands once the menu is drawn: an end of it, or nowhere when the pointer opened it
  const land = useRef<"first" | "last" | null>(null);
  const uid = useId();
  const isOpen = open !== null;

  useEffect(() => {
    if (!isOpen) return;
    openCount++;
    return () => {
      openCount--;
    };
  }, [isOpen]);

  // the menus change with the view (the start window has four): nothing stays open across that
  const count = menus.length;
  useEffect(() => {
    setOpen(null);
    setTab((was) => Math.min(was, count - 1));
  }, [count]);

  const entries = () =>
    Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menu"] [role^="menuitem"]:not([aria-disabled="true"])') ?? []);

  useLayoutEffect(() => {
    if (open === null || !land.current) return;
    const items = entries();
    (land.current === "first" ? items[0] : items[items.length - 1])?.focus();
    land.current = null;
  }, [open]);

  // a press outside, or the window losing the focus, closes the menu where it is
  useEffect(() => {
    if (!isOpen) return;
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(null);
    };
    const away = () => setOpen(null);
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("blur", away);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("blur", away);
    };
  }, [isOpen]);

  const show = (i: number, where: "first" | "last" | null) => {
    land.current = where;
    setTab(i);
    setOpen(i);
  };
  const close = (refocus: boolean) => {
    const was = open;
    setOpen(null);
    if (refocus && was !== null) tops.current[was]?.focus();
  };
  const step = (from: number, by: number) => (from + by + count) % count;

  const onTopKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const next = step(i, e.key === "ArrowRight" ? 1 : -1);
      if (isOpen) show(next, "first");
      else setTab(next);
      tops.current[next]?.focus();
    } else if (e.key === "ArrowDown") show(i, "first");
    else if (e.key === "ArrowUp") show(i, "last");
    else if (e.key === "Home" || e.key === "End") {
      const next = e.key === "Home" ? 0 : count - 1;
      setOpen(null);
      setTab(next);
      tops.current[next]?.focus();
    } else if (e.key === "Escape" && isOpen) close(true);
    else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    if (open === null) return;
    const items = entries();
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const by = e.key === "ArrowDown" ? 1 : -1;
      items[at < 0 ? (by > 0 ? 0 : items.length - 1) : (at + by + items.length) % items.length]?.focus();
    } else if (e.key === "Home") items[0]?.focus();
    else if (e.key === "End") items[items.length - 1]?.focus();
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") show(step(open, e.key === "ArrowRight" ? 1 : -1), "first");
    else if (e.key === "Escape") close(true);
    else if (e.key === "Tab") {
      // the focus goes back to the menu's name first, so Tab then leaves the bar from there
      close(true);
      return;
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") {
      const letter = e.key.toLocaleLowerCase();
      const from = at < 0 ? 0 : at + 1;
      const order = [...items.slice(from), ...items.slice(0, from)];
      order.find((el) => (el.dataset.label ?? "").toLocaleLowerCase().startsWith(letter))?.focus();
    } else return;
    e.preventDefault();
    e.stopPropagation();
  };

  const choose = (item: MenuItem) => {
    if (item.disabled) return;
    // the focus is back on the menu's name before the action runs, so a dialog it opens
    // returns the focus there when it closes
    close(true);
    item.run();
  };

  return (
    <div className="menubar" role="menubar" aria-label={label} ref={root}>
      {menus.map((menu, i) => (
        <div className="menu-slot" key={menu.id}>
          <button
            ref={(el) => { tops.current[i] = el; }}
            className="menu-top"
            role="menuitem"
            aria-haspopup="menu"
            aria-expanded={open === i}
            aria-controls={open === i ? `${uid}-${menu.id}` : undefined}
            tabIndex={i === tab ? 0 : -1}
            // a click made by Enter or Space has no pointer behind it: the focus goes into the menu
            onClick={(e) => (open === i ? close(false) : show(i, e.detail === 0 ? "first" : null))}
            onMouseEnter={() => { if (isOpen && open !== i) show(i, null); }}
            onKeyDown={(e) => onTopKey(e, i)}
          >
            {menu.label}
          </button>
          {open === i && (
            <div className="menu-pop" role="menu" id={`${uid}-${menu.id}`} aria-label={menu.label} onKeyDown={onMenuKey}>
              {sections(menu.items).map((sec, n) => (
                <div className="menu-sec" key={n} role={sec.group ? "group" : "none"} aria-label={sec.group}>
                  {sec.group && <div className="menu-group" aria-hidden="true">{sec.group}</div>}
                  {sec.items.map((item) => (
                    <button
                      key={item.id}
                      className="menu-item"
                      role={roleOf(item)}
                      aria-checked={item.checked}
                      aria-disabled={item.disabled || undefined}
                      aria-current={item.current ? "page" : undefined}
                      data-label={item.label}
                      tabIndex={-1}
                      onClick={() => choose(item)}
                      onMouseEnter={(e) => { if (!item.disabled) e.currentTarget.focus(); }}
                    >
                      <span className="menu-check">{item.checked && <Icon name="check" size={14} />}</span>
                      <span className="menu-label">{item.label}</span>
                      {item.keys && <span className="menu-keys">{item.keys}</span>}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
