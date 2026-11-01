import { useEffect, useRef } from "react";
import { Icon } from "./Icon";
import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";
import { useStore, type Scenario, type Screen } from "../lib/store";

interface Step {
  screen: Screen;
  scenario?: Scenario;
  /** "showcase": the hour the transformer is most loaded without control (/meta) */
  hour?: "showcase";
  /** "example": the house the published year curtails most (/compliance) */
  cid?: "example";
}

/** The two-minute tour: each step puts a real screen in front of the viewer and says what it answers. */
const STEPS: Step[] = [
  { screen: "compare" },
  { screen: "twin", scenario: "uncontrolled", hour: "showcase" },
  { screen: "twin", scenario: "doe", hour: "showcase" },
  { screen: "request", cid: "example" },
  { screen: "report", cid: "example" },
  { screen: "method" },
];
export const TOUR_STEPS = STEPS.length;
const SEEN = "kap.tour";

export function tourSeen(): boolean {
  try {
    return localStorage.getItem(SEEN) === "1";
  } catch {
    return true; // no storage: never pester
  }
}

export function markTourSeen() {
  try {
    localStorage.setItem(SEEN, "1");
  } catch {
    /* private mode */
  }
}

/**
 * The tour card. Not modal: the screen behind it is the point, so it stays usable and Tab
 * reaches it. Each step drives the app (screen, scenario, hour, house) through the store, as a
 * link would. Opened from the keyboard it appears at once; from a click, it rises in.
 */
export function Tour() {
  const t = useT();
  const step = useStore((s) => s.tour);
  const instant = useStore((s) => s.tourInstant);
  const meta = useApi<any>(step !== null ? "/meta" : null);
  const comp = useApi<any>(step !== null ? "/compliance" : null);
  const next = useRef<HTMLButtonElement>(null);
  const close = () => {
    markTourSeen();
    useStore.getState().setTour(null);
  };

  useEffect(() => {
    if (step === null) return;
    const s = STEPS[step];
    const st = useStore.getState();
    if (s.scenario) st.setScenario(s.scenario);
    if (s.hour === "showcase" && typeof meta.data?.showcase_hour === "number") {
      useStore.setState({ hour: meta.data.showcase_hour, hourTouched: true });
    }
    if (s.cid === "example") {
      const cid = comp.data?.data?.per_customer?.[0]?.customer_id;
      if (typeof cid === "number") st.select(cid);
    }
    if (st.screen !== s.screen) st.setScreen(s.screen);
  }, [step, meta.data, comp.data]);

  useEffect(() => {
    if (step !== null) next.current?.focus({ preventScroll: true });
  }, [step]);

  useEffect(() => {
    if (step === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector('[role="dialog"][aria-modal="true"]')) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  if (step === null) return null;
  const last = step === STEPS.length - 1;
  return (
    <aside className={`tour${instant ? " instant" : ""}`} aria-labelledby="tour-title" aria-describedby="tour-text">
      <div className="tour-head">
        <span className="tour-count">{t("tour.of", { n: step + 1, total: STEPS.length })}</span>
        <button className="icon-btn" aria-label={t("tour.close")} title={t("tour.close")} onClick={close}>
          <Icon name="close" size={15} />
        </button>
      </div>
      <h2 id="tour-title">{t(`tour.title.${step + 1}`)}</h2>
      <p id="tour-text">{t(`tour.text.${step + 1}`)}</p>
      <div className="tour-actions">
        {step > 0 && (
          <button className="btn ghost small" onClick={() => useStore.getState().setTour(step - 1)}>
            <Icon name="previous" size={15} />{t("tour.back")}
          </button>
        )}
        <button ref={next} className="btn small" onClick={() => (last ? close() : useStore.getState().setTour(step + 1))}>
          {last ? t("tour.done") : t("tour.next")}{!last && <Icon name="next" size={15} />}
        </button>
      </div>
    </aside>
  );
}

/** Offered once, on a first visit, and never started for the viewer: a shared screen stays still. */
export function TourOffer() {
  const t = useT();
  const offered = useStore((s) => s.tourOffered);
  if (!offered) return null;
  const answer = (take: boolean) => {
    markTourSeen();
    useStore.setState({ tourOffered: false });
    if (take) useStore.getState().setTour(0);
  };
  return (
    <aside className="tour tour-offer" aria-label={t("tour.offerLabel")}>
      <p>{t("tour.offer")}</p>
      <div className="tour-actions">
        <button className="btn small" onClick={() => answer(true)}>{t("tour.offerTake")}</button>
        <button className="btn ghost small" onClick={() => answer(false)}>{t("tour.offerNo")}</button>
      </div>
    </aside>
  );
}
