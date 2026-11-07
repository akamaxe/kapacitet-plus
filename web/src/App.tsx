import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FootLine, MenuSheet, TopBar } from "./components/Shell";
import { useT } from "./lib/i18n";
import { PageSkeleton, SplitSkeleton } from "./components/Skeleton";
import { modalOpen } from "./components/Dialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Palette } from "./components/Palette";
import { Shortcuts } from "./components/Shortcuts";
import { Toasts, toast } from "./components/Toast";
import { Setup } from "./components/Setup";
import { SignIn } from "./components/SignIn";
import { useMe } from "./lib/auth";
import { Tour, TourOffer, tourSeen } from "./components/Tour";
import { useApi } from "./lib/api";
import { SCREENS, initUrlSync, scrollPositions, syncUrl, useStore, type Screen } from "./lib/store";

initUrlSync();

const National = lazy(() => import("./screens/National"));
const Twin = lazy(() => import("./screens/Twin"));
const Compare = lazy(() => import("./screens/Compare"));
const Request = lazy(() => import("./screens/Request"));
const Operation = lazy(() => import("./screens/Operation"));
const Report = lazy(() => import("./screens/Report"));
const Scenarios = lazy(() => import("./screens/Scenarios"));
const Method = lazy(() => import("./screens/Method"));

/**
 * What a screen looks like while its code arrives: the same skeleton the screen shows while
 * its data arrives, so the first visit changes once, not twice.
 */
function ScreenSkeleton({ screen }: { screen: Screen }) {
  if (screen === "national") return <SplitSkeleton wide />;
  if (screen === "twin" || screen === "request") return <SplitSkeleton />;
  if (screen === "compare" || screen === "report" || screen === "operation") return <PageSkeleton bar hero blocks={2} />;
  if (screen === "method") return <PageSkeleton bar blocks={2} />;
  return <PageSkeleton blocks={1} />;
}

/**
 * With sign-in on (KAPACITET_AUTH=oidc), nothing but the sign-in page shows until someone with a
 * Kapacitet+ role is signed in; the screens' own requests would only be refused. With it off, or
 * on a server too old to say, the application opens as it always has.
 */
export default function App() {
  const me = useMe();
  if (me.isPending) return null;
  const gate = me.data?.enabled && !(me.data.user && me.data.user.roles.length > 0);
  return gate ? <SignIn me={me.data!} /> : <Main />;
}

function Main() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  // a fresh clone has no results: say how to make them instead of showing empty screens
  const health = useApi<any>("/health");
  const setupNeeded = health.data && health.data.parts && !health.data.parts.results?.ready;
  // the network the server is on goes into every link; a link made on another one says so once
  const serverDataset: string | undefined = health.data?.dataset;
  useEffect(() => {
    if (!serverDataset) return;
    const st = useStore.getState();
    useStore.setState({ dataset: serverDataset });
    if (st.linkDataset && st.linkDataset !== serverDataset) {
      toast.info(t("nets.linkOther", { link: st.linkDataset, current: serverDataset }));
      useStore.setState({ linkDataset: null });
    }
    syncUrl();
  }, [serverDataset, t]);
  // only once /health has said there is something to draw: before it answers, asking would be a
  // guaranteed failure on a fresh clone
  const net = useApi<any>(health.data && !setupNeeded ? "/network" : null);
  const notFound = useStore((s) => s.notFound);
  const selectedCid = useStore((s) => s.selectedCid);

  // a link to a screen that does not exist opens the first one, and says so once
  useEffect(() => {
    if (!notFound) return;
    toast.info(t("ui.noScreen", { name: notFound }));
    useStore.setState({ notFound: null });
    syncUrl();
  }, [notFound, t]);

  // a link to a house number this neighbourhood does not have: say so and select nothing
  useEffect(() => {
    if (selectedCid === null || !net.data) return;
    const known = net.data.features.some((ft: any) => ft.properties?.layer === "house" && ft.properties.cid === selectedCid);
    if (!known) {
      toast.info(t("ui.noHouse", { id: selectedCid }));
      useStore.getState().select(null);
    }
  }, [selectedCid, net.data, t]);

  // each screen names the document; a change of screen hands the focus to the new screen's
  // heading, so a screen reader announces where it has arrived (not on the first load, and not
  // while the tour holds the focus on its own card)
  const firstScreen = useRef(true);
  useEffect(() => {
    document.title = `${t(`tab.${screen}`)} · Kapacitet+`;
    if (firstScreen.current) {
      firstScreen.current = false;
      return;
    }
    if (useStore.getState().tour !== null) return;
    let tries = 0;
    let raf = 0;
    const go = () => {
      const h = document.querySelector<HTMLElement>("main h1");
      if (h) {
        if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
        h.focus({ preventScroll: true });
      } else if (++tries < 60) raf = requestAnimationFrame(go);
    };
    go();
    return () => cancelAnimationFrame(raf);
  }, [screen, t]);

  // a first visit is offered the tour after a moment; it never starts by itself
  useEffect(() => {
    if (tourSeen()) return;
    const id = window.setTimeout(() => {
      if (!modalOpen() && useStore.getState().tour === null) useStore.setState({ tourOffered: true });
    }, 1500);
    return () => window.clearTimeout(id);
  }, []);
  const [palette, setPalette] = useState(false);
  const [keys, setKeys] = useState(false);
  const [menu, setMenu] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Keys: 1–8 jump to a screen, Ctrl+→/← step, ⌘K or / opens the palette, ? the key
  // sheet, F fullscreen. The sheet in Shortcuts.tsx lists the same set.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      // closest() only exists on elements: a key event whose target is the document
      // itself would otherwise throw and take the whole handler with it.
      const typing = !!target?.closest?.("input, select, textarea, canvas, [contenteditable='true']");
      if (e.altKey) return;
      // under a dialog the keys belong to the dialog: a digit typed under the key sheet
      // must not change the screen behind it
      if (modalOpen()) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        setPalette(true);
        e.preventDefault();
        return;
      }
      if (typing) return;
      const st = useStore.getState();
      const i = SCREENS.indexOf(st.screen);
      // a one-character shortcut answers only while they are on (the key sheet turns them off)
      const plain = !e.ctrlKey && !e.metaKey && e.key.length === 1;
      if (plain && !st.singleKeys) return;
      const digit = Number(e.key);
      if (digit >= 1 && digit <= SCREENS.length && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        st.setScreen(SCREENS[digit - 1]);
        e.preventDefault();
      } else if (e.key === "/" ) {
        setPalette(true);
        e.preventDefault();
      } else if (e.key === "?") {
        setKeys(true);
        e.preventDefault();
      } else if (e.key === "PageDown" || (e.ctrlKey && e.key === "ArrowRight")) {
        st.setScreen(SCREENS[Math.min(SCREENS.length - 1, i + 1)]);
        e.preventDefault();
      } else if (e.key === "PageUp" || (e.ctrlKey && e.key === "ArrowLeft")) {
        st.setScreen(SCREENS[Math.max(0, i - 1)]);
        e.preventDefault();
      } else if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey) {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => undefined);
        else document.exitFullscreen?.().catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Each screen owns its own scroller above 1100 px, so there is no single position to
  // keep: remember one per screen and put it back when the screen comes round again.
  const previous = useRef(screen);
  useLayoutEffect(() => {
    const leaving = previous.current;
    if (leaving !== screen) {
      scrollPositions.set(leaving, lastScroll.current);
      previous.current = screen;
      lastScroll.current = 0;
    }
    // The screen is lazy, so its scroller does not exist yet when this runs. Try for a
    // few frames and give up rather than hold a handle on a screen the viewer has left.
    const want = scrollPositions.get(screen) ?? 0;
    let tries = 0;
    let raf = 0;
    const put = () => {
      const el = document.querySelector<HTMLElement>("main .page");
      // The element appears before its charts and tables have height, so an early
      // assignment clamps to nothing. Keep asking until the position sticks.
      if (el) el.scrollTop = want;
      const settled = el && (want === 0 || Math.abs(el.scrollTop - want) < 2);
      if (!settled && ++tries < 60) raf = requestAnimationFrame(put);
    };
    put();
    return () => cancelAnimationFrame(raf);
  }, [screen, attempt]);

  const lastScroll = useRef(0);
  useEffect(() => {
    const onScroll = (e: Event) => {
      const t = e.target as HTMLElement;
      if (t?.classList?.contains("page")) lastScroll.current = t.scrollTop;
    };
    document.addEventListener("scroll", onScroll, true);
    return () => document.removeEventListener("scroll", onScroll, true);
  }, []);

  return (
    <div className="app">
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>{t("shell.skip")}</a>
      <TopBar onSearch={() => setPalette(true)} onMenu={() => setMenu(true)} />
      <main id="main" tabIndex={-1} aria-label={t(`tab.${screen}`)}>
        {setupNeeded ? <Setup health={health.data} /> : (
        <ErrorBoundary where={t(`tab.${screen}`)} onReset={() => setAttempt((a) => a + 1)} key={`${screen}-${attempt}`}>
          <Suspense fallback={<ScreenSkeleton screen={screen} />}>
            {screen === "national" && <National />}
            {screen === "twin" && <Twin />}
            {screen === "compare" && <Compare />}
            {screen === "request" && <Request />}
            {screen === "operation" && <Operation />}
            {screen === "report" && <Report />}
            {screen === "scenarios" && <Scenarios />}
            {screen === "method" && <Method />}
          </Suspense>
        </ErrorBoundary>
        )}
      </main>
      {/* with nothing computed there is nothing to tour: no offer and no link until the data is in */}
      <FootLine onKeys={() => setKeys(true)} onTour={setupNeeded ? undefined : () => useStore.getState().setTour(0)} />
      {!setupNeeded && <Tour />}
      {!setupNeeded && <TourOffer />}
      <Palette open={palette} onClose={() => setPalette(false)} />
      <Shortcuts open={keys} onClose={() => setKeys(false)} />
      <MenuSheet open={menu} onClose={() => setMenu(false)} />
      <Toasts />
    </div>
  );
}
