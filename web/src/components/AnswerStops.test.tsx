import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnswerStops, heroStops } from "./AnswerStops";

// The opening answer of Static vs envelopes reads its three stops from /hosting and types none of
// them: with /hosting mocked, the screen shows the mocked medians and nothing else.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function hosting(withForesight: boolean) {
  const kw = (median: number) => ({ kw_ac: { median }, adopters: { median: 1 } });
  return {
    data: {
      summary: {
        z_star_pct: 4,
        budget_basis: { doe: "aggregate", doe_installation: "installation" },
        measures: { static: kw(401), timeseries: kw(612), doe: kw(803), doe_installation: kw(733) },
      },
      ...(withForesight ? { foresight: { median_kw: { climatological: 577 } } } : {}),
    },
  };
}

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(payload: unknown): Promise<HTMLElement> {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    expect(url).toBe("/api/v1/hosting");
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}><AnswerStops /></QueryClientProvider>);
  });
  for (let i = 0; i < 20 && !host.querySelector("[data-stop]"); i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
  }
  return host;
}

const stop = (el: HTMLElement, name: string) => el.querySelector(`[data-stop="${name}"]`)?.textContent ?? null;

describe("the opening answer", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    root = null;
    vi.unstubAllGlobals();
  });

  it("shows the mocked medians: static, the issuable range, the bound and the per-installation figure", async () => {
    const el = await render(hosting(true));
    expect(stop(el, "static")).toBe("401kW");
    // the range runs from the lower to the higher of the day-ahead and the hourly figures
    expect(stop(el, "issuable")).toBe("577–612kW");
    expect(stop(el, "bound")).toBe("803kW");
    expect(stop(el, "installation")).toMatch(/^733\skW/);
    // the bound says the budget it is held to
    expect(el.querySelector(".basis-mark")?.textContent).toMatch(/\b4\s%/);
  });

  it("still renders without foresight.json: the issuable stop is the hourly study alone", async () => {
    const el = await render(hosting(false));
    expect(stop(el, "issuable")).toBe("612kW");
    expect(stop(el, "bound")).toBe("803kW");
    expect(el.textContent).toContain("not computed yet");
  });
});

describe("heroStops", () => {
  it("orders the range and leaves out what is not published", () => {
    const s = heroStops(hosting(true).data)!;
    expect(s.issuable).toEqual([577, 612]);
    expect(s.dayAheadKw).toBe(577);
    expect(s.budget).toBe("aggregate");
    const bare = hosting(false).data as any;
    delete bare.summary.measures.doe_installation;
    const b = heroStops(bare)!;
    expect(b.issuable).toEqual([612, 612]);
    expect(b.perInstallationKw).toBeNull();
    expect(heroStops({ summary: { measures: {} } })).toBeNull();
  });
});
