import { useLayoutEffect, useRef, useState } from "react";
import { useApi } from "../lib/api";
import { LANGS, LANG_NAMES, useT } from "../lib/i18n";
import { SCREENS, useStore, type Theme } from "../lib/store";
import { Mark } from "./bits";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";

/** The product's mark: a roundel with the plus of the name, drawn rather than typed. */
/** The brand mark: the top bar's, and the printed record's. */
export function Roundel() {
  return (
    <svg className="roundel" width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
      <circle cx="13" cy="13" r="13" />
      <path d="M13 7v12M7 13h12" />
    </svg>
  );
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => undefined);
  else document.documentElement.requestFullscreen?.().catch(() => undefined);
}

/** Room the folded bar needs beyond the screens' own width before it unfolds again, so a window
 *  resized across the edge does not flicker between the two. */
const UNFOLD_SLACK = 24;

/**
 * Whether the screens fit in the bar, measured rather than guessed: their width depends on the
 * language (Swedish words run long) and on how many screens there are. A hidden probe holds
 * the labels at their widest weight; the bar folds when the room left by the brand and the
 * tools is less than the probe, and unfolds only with some slack.
 */
function useFold(bar: React.RefObject<HTMLElement | null>, probe: React.RefObject<HTMLElement | null>) {
  const [folded, setFolded] = useState(false);
  useLayoutEffect(() => {
    const el = bar.current;
    const pr = probe.current;
    if (!el || !pr) return;
    const check = () => {
      const cs = getComputedStyle(el);
      const gap = parseFloat(cs.columnGap) || 0;
      let used = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
      for (const child of Array.from(el.children) as HTMLElement[]) {
        if (child.contains(pr) || child.classList.contains("topnav") || child.classList.contains("menu-btn")) continue;
        if (getComputedStyle(child).display === "none") continue;
        used += child.getBoundingClientRect().width + gap;
      }
      const free = el.clientWidth - used;
      const need = pr.scrollWidth;
      setFolded((was) => (was ? free < need + UNFOLD_SLACK : free < need));
    };
    const ro = new ResizeObserver(check);
    ro.observe(el);
    ro.observe(pr);
    check();
    return () => ro.disconnect();
  }, [bar, probe]);
  return folded;
}

/**
 * The top bar: the product and where it is, the screens, and the global tools. It is SL blue
 * in both themes, the one place the chrome carries colour. When the screens do not fit they
 * fold into a menu sheet (useFold).
 */
export function TopBar({ onSearch, onMenu }: { onSearch: () => void; onMenu: () => void }) {
  const t = useT();
  // the scope is the site the data describe, read from the data like every other fact
  const meta = useApi<any>("/meta");
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const barRef = useRef<HTMLElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const folded = useFold(barRef, probeRef);
  return (
    <header className="topbar" ref={barRef} data-folded={folded || undefined}>
      {/* a zero-size box clips the probe, so the labels it measures never widen the bar */}
      <div className="topnav-probe-box" aria-hidden="true">
        <div className="topnav-probe" ref={probeRef}>
          {SCREENS.map((s) => <span key={s}>{t(`tab.${s}`)}</span>)}
        </div>
      </div>
      <button className="top-tool menu-btn" onClick={onMenu} aria-label={t("shell.menu")} aria-haspopup="dialog">
        <Icon name="menu" size={20} />
      </button>
      <a className="brand" href="#/compare" aria-label="Kapacitet+">
        <Roundel />
        <span className="brand-name">Kapacitet+</span>
      </a>
      <span className="brand-scope">{meta.data ? `${meta.data.site} · ${meta.data.bidding_zone}` : ""}</span>
      <nav className="topnav" aria-label={t("shell.navigation")}>
        {SCREENS.map((s) => (
          <a key={s} href={`#/${s}`} aria-current={screen === s ? "page" : undefined}>{t(`tab.${s}`)}</a>
        ))}
      </nav>
      <div className="top-tools">
        <button className="top-search" onClick={onSearch} aria-label={t("pal.searchHint")}>
          <Icon name="search" size={16} />
          <span className="top-search-label">{t("pal.searchHint")}</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className="top-lang" role="group" aria-label={t("shell.language")}>
          {LANGS.map((l) => (
            <button key={l} className="top-tool" lang={l} aria-pressed={lang === l} title={LANG_NAMES[l]} onClick={() => void setLang(l)}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
        <select className="top-tool theme-select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label={t("ui.theme")}>
          {(["light", "dark", "system"] as const).map((v) => <option key={v} value={v}>{t(`shell.theme.${v}`)}</option>)}
        </select>
        <button className="top-tool fullscreen-btn" title={`${t("shell.fullscreen")} (F)`} aria-label={t("shell.fullscreen")} onClick={toggleFullscreen}>
          <Icon name="expand" size={18} />
        </button>
      </div>
    </header>
  );
}

/** The screens as a sheet, for a window too narrow to hold them in the top bar. */
export function MenuSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  return (
    <Dialog open={open} onClose={onClose} title={t("shell.menu")} width={420}>
      <nav className="menu-sheet" aria-label={t("shell.navigation")}>
        {SCREENS.map((s, i) => (
          <a key={s} href={`#/${s}`} aria-current={screen === s ? "page" : undefined} onClick={onClose}>
            <span>{t(`tab.${s}`)}</span>
            <kbd>{i + 1}</kbd>
          </a>
        ))}
      </nav>
      <div className="menu-prefs">
        <div className="seg" role="group" aria-label={t("shell.language")}>
          {LANGS.map((l) => (
            <button key={l} lang={l} aria-pressed={lang === l} onClick={() => void setLang(l)}>{LANG_NAMES[l]}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label={t("ui.theme")}>
          {(["light", "dark", "system"] as const).map((v) => (
            <button key={v} aria-pressed={theme === v} onClick={() => setTheme(v)}>{t(`shell.theme.${v}`)}</button>
          ))}
        </div>
      </div>
    </Dialog>
  );
}

/**
 * The foot line: which data are surveyed and which are assumed, on what year, and the key
 * sheet. Quiet, and the same on every screen.
 */
export function FootLine({ onKeys, onTour }: { onKeys: () => void; onTour?: () => void }) {
  const t = useT();
  const meta = useApi<any>("/meta");
  return (
    <footer className="footline">
      <span className="fl-legend">
        <span><Mark kind="real" />{t("tb.real")}</span>
        <span><Mark kind="synthetic" />{t("tb.synthetic")}</span>
      </span>
      <span className="fl-year">
        {t("tb.year")} <span className="num">{meta.data?.reference_year ?? "–"}</span>
      </span>
      {onTour && <button className="fl-keys" onClick={onTour}>{t("tour.start")}</button>}
      <button className="fl-keys" onClick={onKeys} aria-haspopup="dialog">
        {t("tb.keys")}<kbd>?</kbd>
      </button>
    </footer>
  );
}
