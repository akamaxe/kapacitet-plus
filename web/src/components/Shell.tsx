import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";
import { SCREENS, useStore, type Theme } from "../lib/store";
import { Mark } from "./bits";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";

/** The product's mark: a roundel with the plus of the name, drawn rather than typed. */
function Roundel() {
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

/**
 * The top bar: the product and where it is, the six screens, and the global tools. It is
 * SL blue in both themes, the one place the chrome carries colour. Below 1100 px the
 * screens fold into a menu sheet.
 */
export function TopBar({ onSearch, onMenu }: { onSearch: () => void; onMenu: () => void }) {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const notes = useStore((s) => s.notes);
  const toggleNotes = useStore((s) => s.toggleNotes);
  return (
    <header className="topbar">
      <button className="top-tool menu-btn" onClick={onMenu} aria-label={t("shell.menu")} aria-haspopup="dialog">
        <Icon name="menu" size={20} />
      </button>
      <a className="brand" href="#/compare" aria-label="Kapacitet+">
        <Roundel />
        <span className="brand-name">Kapacitet+</span>
      </a>
      <span className="brand-scope">{t("tb.site")}</span>
      <nav className="topnav" aria-label={t("shell.navigation")}>
        {SCREENS.map((s) => (
          <a key={s} href={`#/${s}`} aria-current={screen === s ? "page" : undefined}>{t(`tab.${s}`)}</a>
        ))}
      </nav>
      <div className="top-tools">
        <button className="top-search" onClick={onSearch}>
          <Icon name="search" size={16} />
          <span className="top-search-label">{t("pal.searchHint")}</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button className="top-tool" onClick={toggleNotes} aria-pressed={notes} title={`${t("ui.notes")} (N)`} aria-label={t("ui.notes")}>
          <Icon name="notes" size={18} />
        </button>
        <button className="top-tool lang" onClick={() => setLang(lang === "en" ? "es" : "en")} aria-label={t("shell.language")} title={t("shell.language")}>
          {lang === "en" ? "ES" : "EN"}
        </button>
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

/** The six screens as a sheet, for a window too narrow to hold them in the top bar. */
export function MenuSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const screen = useStore((s) => s.screen);
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
    </Dialog>
  );
}

/**
 * The foot line: which data are surveyed and which are assumed, on what year, and the key
 * sheet. Quiet, and the same on every screen.
 */
export function FootLine({ onKeys }: { onKeys: () => void }) {
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
      <button className="fl-keys" onClick={onKeys} aria-haspopup="dialog">
        {t("tb.keys")}<kbd>?</kbd>
      </button>
    </footer>
  );
}
