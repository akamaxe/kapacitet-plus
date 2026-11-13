import { useLayoutEffect, useRef, useState } from "react";
import { useApi } from "../lib/api";
import { useMe, useSignOut } from "../lib/auth";
import { LANGS, LANG_NAMES, useT } from "../lib/i18n";
import type { Menu } from "../lib/menus";
import { useProjectT } from "../lib/project";
import { useStore } from "../lib/store";
import { Mark, Status } from "./bits";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { Logo } from "./Logo";
import { MenuBar, sections } from "./Menu";

/**
 * Who is signed in and the roles they act under, with sign-in on; nothing with it off. The name
 * gives way first when the bar is narrow, the roles stay.
 */
function UserChip({ inSheet = false }: { inSheet?: boolean }) {
  const t = useT();
  const me = useMe().data;
  const signOut = useSignOut();
  if (!me?.enabled || !me.user) return null;
  const roles = me.user.roles.map((r) => t(`auth.role.${r}`)).join(", ");
  return (
    <div className={inSheet ? "menu-user" : "top-user"} role="group" aria-label={t("auth.signedIn")}>
      <span className="top-user-who" title={`${me.user.name} · ${roles}`}>
        <span className="top-user-name">{me.user.name}</span>
        <span className="top-user-role">{roles}</span>
      </span>
      <button className={inSheet ? "btn ghost small" : "top-tool"} onClick={() => void signOut()}>{t("auth.signOut")}</button>
    </div>
  );
}

export function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => undefined);
  else document.documentElement.requestFullscreen?.().catch(() => undefined);
}

/** Room the folded bar needs beyond the menus' own width before it unfolds again, so a window
 *  resized across the edge does not flicker between the two. */
const UNFOLD_SLACK = 24;

/**
 * Whether the menus fit in the bar, measured rather than guessed: their width depends on the
 * language and on how many there are (the start window has four, a project six). A hidden probe
 * holds their names; the bar folds when the room left by the logo and the tools is less than
 * the probe, and unfolds only with some slack.
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
        if (child.contains(pr) || child.classList.contains("menubar") || child.classList.contains("menu-btn")) continue;
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
 * The bar at the top: the logo, which leads to the start window, the menus, the project that is
 * open and the search. It is SL blue in both themes, the one place the chrome carries colour.
 * When the menus do not fit they fold into a sheet (useFold).
 */
export function AppBar({ menus, onSearch, onMenu, onProject }: {
  menus: Menu[]; onSearch: () => void; onMenu: () => void; onProject: () => void;
}) {
  const t = useT();
  // the project is the one the data describe, read from the data like every other fact
  const meta = useApi<any>("/meta");
  const view = useStore((s) => s.view);
  const barRef = useRef<HTMLElement>(null);
  const probeRef = useRef<HTMLDivElement>(null);
  const folded = useFold(barRef, probeRef);
  return (
    <header className="topbar" ref={barRef} data-folded={folded || undefined}>
      {/* a zero-size box clips the probe, so the names it measures never widen the bar */}
      <div className="menubar-probe-box" aria-hidden="true">
        <div className="menubar-probe" ref={probeRef}>
          {menus.map((m) => <span key={m.id}>{m.label}</span>)}
        </div>
      </div>
      <button className="top-tool menu-btn" onClick={onMenu} aria-label={t("shell.menu")} aria-haspopup="dialog">
        <Icon name="menu" size={20} />
      </button>
      <a className="brand" href="#/start" aria-label={t("start.go")}>
        <Logo tone="reversed" />
      </a>
      <MenuBar menus={menus} label={t("shell.menus")} />
      <div className="top-tools">
        {view === "work" && meta.data && (
          <button className="top-project" onClick={onProject} title={t("menu.file.properties")} aria-haspopup="dialog">
            {meta.data.site}
          </button>
        )}
        <button className="top-search" onClick={onSearch} aria-label={t("pal.searchHint")}>
          <Icon name="search" size={16} />
          <span className="top-search-label">{t("pal.searchHint")}</span>
          <kbd>Ctrl K</kbd>
        </button>
        <UserChip />
      </div>
    </header>
  );
}

/** The menus as a sheet, for a window too narrow to hold them in the bar. */
export function MenuSheet({ open, onClose, menus }: { open: boolean; onClose: () => void; menus: Menu[] }) {
  const t = useT();
  return (
    <Dialog open={open} onClose={onClose} title={t("shell.menu")} width={420}>
      <nav className="menu-sheet" aria-label={t("shell.menus")}>
        {menus.map((menu) => (
          <section key={menu.id} aria-labelledby={`sheet-${menu.id}`}>
            <h2 id={`sheet-${menu.id}`}>{menu.label}</h2>
            {sections(menu.items).map((sec, n) => (
              <div className="menu-sec" key={n} role={sec.group ? "group" : undefined} aria-label={sec.group}>
                {sec.group && <div className="menu-group" aria-hidden="true">{sec.group}</div>}
                {sec.items.map((item) => (
                  <button
                    key={item.id}
                    className="menu-item"
                    aria-pressed={item.checked}
                    aria-current={item.current ? "page" : undefined}
                    disabled={item.disabled}
                    onClick={() => { onClose(); item.run(); }}
                  >
                    <span className="menu-check">{item.checked && <Icon name="check" size={16} />}</span>
                    <span className="menu-label">{item.label}</span>
                    {item.keys && <span className="menu-keys">{item.keys}</span>}
                  </button>
                ))}
              </div>
            ))}
          </section>
        ))}
      </nav>
      <UserChip inSheet />
    </Dialog>
  );
}

/**
 * The status bar: whether the server answers and its version, the project and the year its
 * figures are for, which data are surveyed and which assumed, the language and the key sheet.
 * Quiet, and the same on every screen.
 */
export function StatusBar({ onKeys }: { onKeys: () => void }) {
  const t = useT();
  const pt = useProjectT();
  const view = useStore((s) => s.view);
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const meta = useApi<any>("/meta");
  // asked again every half minute, so the bar says when the server has gone
  const health = useApi<any>("/health", { refetchMs: 30_000 });
  return (
    <footer className="statusbar">
      <span className="sb-conn" role="status">
        {health.isError ? <Status kind="critical">{t("sb.down")}</Status> : <span>{t(health.data ? "sb.ok" : "sb.wait")}</span>}
        {health.data?.version && <span className="sb-version num">v{health.data.version}</span>}
      </span>
      {view === "work" && meta.data && (
        <span className="sb-project">
          <span className="sb-name">{meta.data.site}</span>
          <span>{meta.data.bidding_zone}</span>
          <span>{t("tb.year")} <span className="num">{meta.data.reference_year}</span></span>
        </span>
      )}
      <span className="sb-legend">
        <span><Mark kind="real" />{pt("tb.real")}</span>
        <span><Mark kind="synthetic" />{pt("tb.synthetic")}</span>
      </span>
      <div className="sb-lang" role="group" aria-label={t("shell.language")}>
        {LANGS.map((l) => (
          <button key={l} className="sb-btn" lang={l} aria-pressed={lang === l} title={LANG_NAMES[l]} onClick={() => void setLang(l)}>
            {l.toUpperCase()}
          </button>
        ))}
      </div>
      <button className="sb-btn" onClick={onKeys} aria-haspopup="dialog">
        {t("tb.keys")}<kbd>?</kbd>
      </button>
    </footer>
  );
}
