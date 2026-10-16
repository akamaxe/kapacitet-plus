import { useApi } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { SCREENS, useStore, type Theme } from "../lib/store";
import { Icon } from "./Icon";

export function SideNav() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const notes = useStore((s) => s.notes);
  const toggleNotes = useStore((s) => s.toggleNotes);
  return (
    <aside className="sidebar">
      <a className="brand" href="#/compare" aria-label="Kapacitet+">
        <span className="brand-symbol"><Icon name="grid" size={25} /></span>
        <span className="brand-word"><span className="brand-mark">kapacitet<span>+</span></span><span className="brand-tag">{t("shell.tagline")}</span></span>
      </a>
      <span className="nav-label">{t("shell.workspace")}</span>
      <nav className="tabs" aria-label={t("shell.navigation")}>
        {SCREENS.map((s, i) => (
          <a key={s} href={`#/${s}`} className="tab" aria-current={screen === s ? "page" : undefined} title={t(`tab.${s}`)}>
            <Icon name={s} /><span>{t(`tab.${s}`)}</span><kbd>{i + 1}</kbd>
          </a>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="site-card"><span className="site-glyph"><Icon name="twin" size={25} /></span><div><strong>Smedslätten</strong><span>{t("shell.place")}</span></div><span className="site-caption">{t("shell.site")}</span></div>
        <button className="notes-toggle" aria-pressed={notes} onClick={toggleNotes} title={`${t("ui.notes")} (N)`}><Icon name="notes" /><span>{t("ui.notes")}</span><kbd>N</kbd></button>
        <span className="project-credit">KTH EI2600 <span>{t("shell.research")}</span></span>
      </div>
    </aside>
  );
}

function LiveStrip() {
  const t = useT();
  const f = useFmt();
  const { data, isError } = useApi<any>("/now", { refetchMs: 10 * 60 * 1000 });
  const today = new Date().toISOString().slice(0, 10);
  const rows: { start: string; sek_kwh: number }[] = data?.prices?.[today] ?? [];
  const cur = rows.filter((r) => new Date(r.start).getTime() <= Date.now()).pop();
  const online = !!data?.online;
  return (
    <div className="live" title="SMHI (CC BY 4.0) · Elpriser tillhandahålls av Elpriset just nu.se">
      <span><span className={`live-dot ${online ? "" : "off"}`} />{online ? t("shell.connected") : data || isError ? t("shell.cached") : t("shell.connecting")}</span>
      {online && data.temperature?.value_c != null && <span>{t("live.temp")} <strong>{f(data.temperature.value_c, 1)} °C</strong></span>}
      {online && cur && <span>SE3 <strong>{f(cur.sek_kwh, 2)} kr/kWh</strong></span>}
    </div>
  );
}

export function TopBar() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  return (
    <header className="topbar">
      <div className="breadcrumb"><span>{t("shell.workspace")}</span><span>/</span><strong>{t(`tab.${screen}`)}</strong></div>
      <div className="topbar-right">
        <LiveStrip /><div className="toolbar-divider" />
        <button className="icon-btn language-btn" onClick={() => setLang(lang === "en" ? "es" : "en")} aria-label={t("shell.language")} title={t("shell.language")}>{lang === "en" ? "ES" : "EN"}</button>
        <select className="theme-select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label={t("ui.theme")}>
          {(["dark", "light", "system"] as const).map((v) => <option key={v} value={v}>{t(`shell.theme.${v}`)}</option>)}
        </select>
        <button className="icon-btn fullscreen-btn" title={`${t("shell.fullscreen")} (F)`} aria-label={t("shell.fullscreen")} onClick={() => {
          if (document.fullscreenElement) document.exitFullscreen?.().catch(() => undefined);
          else document.documentElement.requestFullscreen?.().catch(() => undefined);
        }}><Icon name="expand" size={17} /></button>
      </div>
    </header>
  );
}

export function StatusBar() {
  const t = useT();
  const meta = useApi<any>("/meta");
  return <footer className="statusbar"><span><Icon name="grid" size={13} />{t("shell.model")}</span><span>{meta.data?.reference_year ? t("shell.year", { year: meta.data.reference_year }) : t("shell.research")}</span><span className="statusbar-hint">{t("shell.shortcuts")}</span></footer>;
}
