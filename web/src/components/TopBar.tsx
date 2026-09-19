import { useApi } from "../lib/api";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { SCREENS, useStore, type Screen } from "../lib/store";

const TAB_KEYS: Record<Screen, string> = {
  national: "tab.national",
  twin: "tab.twin",
  compare: "tab.compare",
  request: "tab.request",
  scenarios: "tab.scenarios",
  method: "tab.method",
};

function LiveStrip() {
  const t = useT();
  const f = useFmt();
  const { data } = useApi<any>("/now", { refetchMs: 10 * 60 * 1000 });
  if (!data) return null;
  const temp = data.temperature?.value_c;
  const today = new Date().toISOString().slice(0, 10);
  const rows: { start: string; sek_kwh: number }[] = data.prices?.[today] ?? [];
  const nowMs = Date.now();
  const cur = rows.filter((r) => new Date(r.start).getTime() <= nowMs).pop();
  if (!data.online) {
    return (
      <div className="live">
        <span>
          <span className="live-dot off" />
          {t("live.offline")}
        </span>
      </div>
    );
  }
  return (
    <div className="live" title="SMHI (CC BY 4.0) · Elpriser tillhandahålls av Elpriset just nu.se">
      {temp !== undefined && (
        <span>
          <span className="live-dot" />
          {t("live.temp")} <strong>{f(temp, 1)} °C</strong>
        </span>
      )}
      {cur && (
        <span>
          {t("live.price")} <strong>{f(cur.sek_kwh, 2)} kr/kWh</strong>
        </span>
      )}
    </div>
  );
}

export function TopBar() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const setScreen = useStore((s) => s.setScreen);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const notes = useStore((s) => s.notes);
  const toggleNotes = useStore((s) => s.toggleNotes);

  const nextTheme = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const themeLabel = theme === "light" ? "☀" : theme === "dark" ? "☾" : "◐";

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">
          Kapacitet<span>+</span>
        </span>
        <span className="brand-site">{t("brand.site")}</span>
      </div>
      <nav className="tabs" role="tablist" aria-label="Screens">
        {SCREENS.map((s, i) => (
          <button key={s} role="tab" className="tab" aria-selected={screen === s} onClick={() => setScreen(s)}>
            <b>{i + 1}</b>
            {t(TAB_KEYS[s])}
          </button>
        ))}
      </nav>
      <div className="topbar-right">
        <LiveStrip />
        <button className="icon-btn" onClick={() => setLang(lang === "en" ? "es" : "en")} aria-label="Language">
          {lang === "en" ? "ES" : "EN"}
        </button>
        <button className="icon-btn" onClick={() => setTheme(nextTheme)} aria-label={`${t("ui.theme")}: ${theme}`} title={`${t("ui.theme")}: ${theme}`}>
          {themeLabel}
        </button>
        <button className="icon-btn" aria-pressed={notes} onClick={toggleNotes} title="N">
          {t("ui.notes")}
        </button>
      </div>
    </header>
  );
}
