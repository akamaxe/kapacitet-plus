/**
 * Find anything from the keyboard: the six screens, the 120 houses by address, and the
 * handful of actions the chrome offers.
 *
 * The houses are the reason this exists. Until now the only way to reach one was to spot
 * its roof among 120 on a pitched map; an operator handed an address had nowhere to type
 * it. The data was already in `/network` — every house carries its address, feeder, size
 * and whether its connection is firm.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { useApi } from "../lib/api";
import { copyText } from "../lib/clipboard";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { SCREENS, applyTheme, useStore, type Screen } from "../lib/store";
import { toast } from "./Toast";

type Item = {
  id: string;
  group: string;
  label: string;
  /** Right-hand columns, not a sentence: a row here reads like a table row. */
  hint?: string;
  cols?: string[];
  icon: Parameters<typeof Icon>[0]["name"];
  run: () => void;
};

/** Fold the Swedish diacritics so "slatten" finds "Smedslättsvägen". */
const fold = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

export function Palette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const f = useFmt();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const net = useApi<any>(open ? "/network" : null);
  const setScreen = useStore((s) => s.setScreen);
  const select = useStore((s) => s.select);
  const toggleNotes = useStore((s) => s.toggleNotes);
  const setLang = useStore((s) => s.setLang);
  const setTheme = useStore((s) => s.setTheme);
  const lang = useStore((s) => s.lang);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const go = (s: Screen) => () => {
      setScreen(s);
      onClose();
    };
    const screens: Item[] = SCREENS.map((s, i) => ({
      id: `screen:${s}`,
      group: t("pal.screens"),
      label: t(`tab.${s}`),
      hint: String(i + 1),
      icon: s,
      run: go(s),
    }));

    const houses: Item[] = (net.data?.features ?? [])
      .filter((ft: any) => ft.properties?.layer === "house")
      .map((ft: any) => {
        const p = ft.properties;
        const status =
          p.pv_status === "firm" ? t("pal.firm") : p.pv_status === "nonfirm" ? t("pal.nonfirm") : t("pal.noPv");
        return {
          id: `house:${p.cid}`,
          group: t("pal.houses"),
          label: p.address || `#${p.cid}`,
          cols: [t("pal.feeder", { n: p.feeder + 1 }), `${f(p.kwp, 1)} kWp`, status],
          icon: "twin" as const,
          run: () => {
            select(p.cid);
            setScreen("twin");
            onClose();
          },
        };
      });

    const actions: Item[] = [
      {
        id: "act:theme",
        group: t("pal.actions"),
        label: t("pal.theme"),
        icon: "sun",
        run: () => {
          const next = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
          setTheme(next);
          applyTheme(next);
          onClose();
        },
      },
      {
        id: "act:lang",
        group: t("pal.actions"),
        label: t("pal.language"),
        icon: "method",
        run: () => {
          setLang(lang === "es" ? "en" : "es");
          onClose();
        },
      },
      {
        id: "act:notes",
        group: t("pal.actions"),
        label: t("ui.notes"),
        hint: "N",
        icon: "notes",
        run: () => {
          toggleNotes();
          onClose();
        },
      },
      {
        id: "act:link",
        group: t("pal.actions"),
        label: t("pal.copyLink"),
        icon: "copy",
        run: () => {
          copyText(window.location.href)
            .then(() => toast.info(t("pal.copied")))
            .catch(() => toast.error(t("pal.copyFailed")));
          onClose();
        },
      },
    ];
    return [...screens, ...houses, ...actions];
  }, [net.data, t, f, lang, setScreen, select, toggleNotes, setLang, setTheme, onClose]);

  const shown = useMemo(() => {
    const needle = fold(q.trim());
    if (!needle) return items.filter((i) => !i.id.startsWith("house:")).concat(items.filter((i) => i.id.startsWith("house:")).slice(0, 5));
    const scored = items
      .map((i) => {
        const hay = fold(i.label);
        const at = hay.indexOf(needle);
        if (at >= 0) return { i, rank: at === 0 ? 0 : 1 };
        return fold([i.hint, ...(i.cols ?? [])].join(" ")).includes(needle) ? { i, rank: 2 } : null;
      })
      .filter(Boolean) as { i: Item; rank: number }[];
    scored.sort((a, b) => a.rank - b.rank);
    return scored.slice(0, 40).map((s) => s.i);
  }, [items, q]);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active, shown]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % Math.max(1, shown.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      shown[active]?.run();
    }
  };

  let lastGroup = "";
  return (
    <Dialog open={open} onClose={onClose} title={t("pal.title")} width={620}>
      <div className="palette">
        <div className="palette-field">
          <Icon name="search" size={18} />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder={t("pal.placeholder")}
            aria-label={t("pal.title")}
            aria-controls="palette-list"
            aria-activedescendant={shown[active] ? `pal-${shown[active].id}` : undefined}
          />
          <kbd>Esc</kbd>
        </div>
        <ul className="palette-list" id="palette-list" role="listbox" ref={listRef} aria-label={t("pal.title")}>
          {shown.length === 0 && <li className="palette-none">{t("pal.none", { q })}</li>}
          {shown.map((i, n) => {
            const head = i.group !== lastGroup ? ((lastGroup = i.group), i.group) : null;
            return (
              <li key={i.id}>
                {head && <div className="palette-group">{head}</div>}
                <div
                  id={`pal-${i.id}`}
                  role="option"
                  aria-selected={n === active}
                  className="palette-item"
                  onMouseEnter={() => setActive(n)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    i.run();
                  }}
                >
                  <Icon name={i.icon} size={17} />
                  <span className="palette-label">{i.label}</span>
                  {i.cols?.map((c) => (
                    <span key={c} className="palette-col">
                      {c}
                    </span>
                  ))}
                  {i.hint && <span className="palette-hint">{i.hint}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </Dialog>
  );
}
