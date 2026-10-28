import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";
import { SCREENS, useStore, type Theme } from "../lib/store";
import { Mark } from "./bits";
import { Icon } from "./Icon";

/**
 * The sheet index (design-system.md §3.4): the wordmark and the site, the six sheets of the
 * set as text, and the quiet global tools. No sidebar, no icons in the index, no badges.
 */
export function SheetIndex({ onSearch }: { onSearch: () => void }) {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  return (
    <header className="index">
      <a className="wordmark" href="#/compare" aria-label="Kapacitet+">
        Kapacitet<span>+</span>
      </a>
      <span className="index-site">{t("shell.siteLine")}</span>
      <nav className="sheets" aria-label={t("shell.navigation")}>
        {SCREENS.map((s) => (
          <a key={s} href={`#/${s}`} aria-current={screen === s ? "page" : undefined}>{t(`tab.${s}`)}</a>
        ))}
      </nav>
      <div className="index-tools">
        <button className="tool" onClick={onSearch} title={`${t("pal.title")} (Ctrl + K)`}>
          <Icon name="search" size={16} /><span className="tool-label">{t("pal.search")}</span>
        </button>
        <button className="tool" onClick={() => setLang(lang === "en" ? "es" : "en")} aria-label={t("shell.language")} title={t("shell.language")}>
          {lang === "en" ? "ES" : "EN"}
        </button>
        <select className="tool theme-select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label={t("ui.theme")}>
          {(["light", "dark", "system"] as const).map((v) => <option key={v} value={v}>{t(`shell.theme.${v}`)}</option>)}
        </select>
        <button className="tool fullscreen-btn" title={`${t("shell.fullscreen")} (F)`} aria-label={t("shell.fullscreen")} onClick={() => {
          if (document.fullscreenElement) document.exitFullscreen?.().catch(() => undefined);
          else document.documentElement.requestFullscreen?.().catch(() => undefined);
        }}><Icon name="expand" size={16} /></button>
      </div>
    </header>
  );
}

/**
 * The title block at the foot of every sheet (design-system.md §3.4): which sheet this is,
 * where, on what year, and which of its data are surveyed and which are assumed.
 */
export function TitleBlock({ onKeys }: { onKeys: () => void }) {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const meta = useApi<any>("/meta");
  const n = SCREENS.indexOf(screen) + 1;
  return (
    <footer className="titleblock">
      <span className="tb-cell tb-sheet">
        <span className="tb-key">{t("tb.sheet")}</span>
        <span className="num">{t("tb.of", { n, total: SCREENS.length })}</span>
      </span>
      <span className="tb-cell tb-title">{t(`tab.${screen}`)}</span>
      <span className="tb-cell tb-site">{t("tb.site")}</span>
      {meta.data?.reference_year ? (
        <span className="tb-cell tb-year">
          <span className="tb-key">{t("tb.year")}</span>
          <span className="num">{meta.data.reference_year}</span>
        </span>
      ) : null}
      <span className="tb-cell tb-legend">
        <span><Mark kind="real" />{t("tb.real")}</span>
        <span><Mark kind="synthetic" />{t("tb.synthetic")}</span>
      </span>
      <span className="tb-cell tb-course">KTH EI2600</span>
      <button className="tb-cell tb-keys" onClick={onKeys} aria-haspopup="dialog">
        {t("tb.keys")}<kbd>?</kbd>
      </button>
    </footer>
  );
}
