import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hashFor, initUrlSync, parseHash, useStore, type LinkedView } from "./store";

// The URL rule (CLAUDE.md): the address describes what is on screen, a missing parameter means
// "unchanged", only a change of screen makes a history entry, and the hour is written at most
// once per 250 ms and never while playing.

describe("parseHash", () => {
  it("reads a whole view", () => {
    expect(parseHash("#/twin?hour=100&scenario=static&cid=5")).toEqual({
      screen: "twin", hour: 100, scenario: "static", cid: 5,
    });
  });

  it("falls back to the opening screen and remembers the name it did not know", () => {
    expect(parseHash("#/nowhere")).toEqual({ screen: "compare", notFound: "nowhere" });
    expect(parseHash("")).toEqual({ screen: "compare" });
  });

  it("reads a missing or empty parameter as unchanged, never as zero", () => {
    const v = parseHash("#/twin?hour=&cid=");
    expect(v.hour).toBeUndefined();
    expect(v.cid).toBeUndefined();
  });

  it("ignores values out of range or not whole", () => {
    expect(parseHash("#/twin?hour=8784").hour).toBeUndefined();
    expect(parseHash("#/twin?hour=-1").hour).toBeUndefined();
    expect(parseHash("#/twin?hour=12.5").hour).toBeUndefined();
    expect(parseHash("#/twin?scenario=bogus").scenario).toBeUndefined();
  });

  it("reads a basis and a rule only from their own lists", () => {
    expect(parseHash("#/report?basis=dayahead&policy=prorata")).toEqual({ screen: "report", basis: "dayahead", policy: "prorata" });
    expect(parseHash("#/report?basis=hindsight&policy=cheapest")).toEqual({ screen: "report" });
  });

  it("takes only a saved assessment's id as a job", () => {
    expect(parseHash("#/request?job=0123456789ab").job).toBe("0123456789ab");
    expect(parseHash("#/request?job=../../etc").job).toBeUndefined();
  });
});

describe("hashFor", () => {
  const base: LinkedView = { screen: "twin", hourTouched: false, hour: 4000, scenario: "doe", selectedCid: null, job: null };

  it("keeps the hour only where it is read, and only once the viewer moved it", () => {
    expect(hashFor(base)).toBe("#/twin");
    expect(hashFor({ ...base, hourTouched: true })).toBe("#/twin?hour=4000");
    expect(hashFor({ ...base, screen: "method", hourTouched: true })).toBe("#/method");
  });

  it("writes the scenario only on the neighbourhood and only away from its default", () => {
    expect(hashFor({ ...base, scenario: "static" })).toBe("#/twin?scenario=static");
    expect(hashFor({ ...base, screen: "compare", scenario: "static" })).toBe("#/compare");
  });

  it("keeps the house where a house is read, and the assessment only on the request", () => {
    expect(hashFor({ ...base, selectedCid: 7 })).toBe("#/twin?cid=7");
    expect(hashFor({ ...base, screen: "report", selectedCid: 7 })).toBe("#/report?cid=7");
    expect(hashFor({ ...base, screen: "method", selectedCid: 7 })).toBe("#/method");
    expect(hashFor({ ...base, screen: "request", selectedCid: 7, job: "0123456789ab" })).toBe("#/request?cid=7&job=0123456789ab");
    expect(hashFor({ ...base, screen: "twin", job: "0123456789ab" })).toBe("#/twin");
  });

  it("carries the basis away from its default and a chosen rule, on the report", () => {
    expect(hashFor({ ...base, screen: "report", basis: "foresight", policy: null })).toBe("#/report");
    expect(hashFor({ ...base, screen: "report", basis: "dayahead", policy: "prorata" })).toBe("#/report?basis=dayahead&policy=prorata");
    expect(hashFor({ ...base, screen: "twin", basis: "dayahead", policy: "prorata" })).toBe("#/twin");
  });

  it("round-trips through parseHash", () => {
    const view: LinkedView = { ...base, screen: "request", hourTouched: true, hour: 123, selectedCid: 3, job: "abcdefabcdef" };
    expect(parseHash(hashFor(view))).toEqual({ screen: "request", hour: 123, cid: 3, job: "abcdefabcdef" });
  });
});

describe("writing the address", () => {
  let replace: ReturnType<typeof vi.spyOn>;
  let push: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    useStore.setState({ screen: "compare", playing: false, hourTouched: false, selectedCid: null, job: null, scenario: "doe" });
    replace = vi.spyOn(window.history, "replaceState");
    push = vi.spyOn(window.history, "pushState");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("makes a history entry only for a change of screen", () => {
    useStore.getState().setScreen("twin");
    expect(push).toHaveBeenCalledTimes(1);
    expect(push.mock.calls[0][2]).toBe("#/twin");
    useStore.getState().setScenario("static");
    expect(push).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenLastCalledWith(null, "", "#/twin?scenario=static");
  });

  it("writes a run of hour changes once, 250 ms after the last", () => {
    useStore.getState().setScreen("twin");
    replace.mockClear();
    const { setHour } = useStore.getState();
    setHour(10);
    setHour(11);
    setHour(12);
    vi.advanceTimersByTime(249);
    expect(replace).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace.mock.calls[0][2]).toBe("#/twin?hour=12");
  });

  it("writes nothing while playing, and once when playback stops", () => {
    useStore.getState().setScreen("twin");
    useStore.getState().setPlaying(true);
    replace.mockClear();
    useStore.getState().setHour(500);
    useStore.getState().setHour(501);
    vi.advanceTimersByTime(1000);
    expect(replace).not.toHaveBeenCalled();
    useStore.getState().setPlaying(false);
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace.mock.calls[0][2]).toBe("#/twin?hour=501");
  });
});

// the applicant's portal (#/apply) shares the store for its language; until the application starts
// following the address, nothing may rewrite the portal's address into a screen
describe("the portal's address", () => {
  it("is left alone until the application asks to follow the address", () => {
    useStore.setState({ screen: "operation" });
    window.location.hash = "#/apply?ref=KP-1A2B-3C4D";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(window.location.hash).toBe("#/apply?ref=KP-1A2B-3C4D");
    expect(useStore.getState().screen).toBe("operation");
  });

  it("is followed once the application starts", () => {
    initUrlSync();
    initUrlSync(); // once is enough, however often it is asked
    window.location.hash = "#/twin";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(useStore.getState().screen).toBe("twin");
  });
});
