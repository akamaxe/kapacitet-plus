import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DecisionBlock, QueueList, type QueueState } from "./Queue";

// The planner's decision on an answer. Where the envelopes are off there is no envelope to connect
// on: firm by the static study, firm by the hourly study (no hour of any weather year over a limit
// with all it asks for), or decline (task 1.4 of the technical review of 14-11-2026).
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const EMPTY: QueueState = { version: 0, baseline: null, items: [], approved: 0, updating: false };
const years = (held: number[]) => held.map((h, i) => ({ year: 2015 + i, hours_limited: h }));
const answer = (staticOk: boolean, held: number[]) => ({
  id: "a00000000001", request: { cid: 3, pv_kwp: 10, baseline: "static" },
  static: { pv: { approved: staticOk } }, per_year: years(held),
});

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(node: React.ReactNode, envelopes: boolean): Promise<HTMLElement> {
  const health = { features: { envelopes, national: false, scenarios: false, portal: false } };
  const me = { enabled: false, user: null, can: {}, needs: {} };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = url.endsWith("/health") ? health : url.endsWith("/auth/me") ? me : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  });
  for (let i = 0; i < 20; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  return host;
}

const buttons = (el: HTMLElement) => [...el.querySelectorAll(".decision-actions button")] as HTMLButtonElement[];
const label = (b: HTMLButtonElement) => (b.textContent ?? "").trim();
const text = (el: Element) => (el.textContent ?? "").replace(/\s+/g, " ");

describe("the decision on an answer", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    document.body.innerHTML = "";
    root = null;
    vi.unstubAllGlobals();
  });

  it("offers firm by the hourly study where the static study rejects and no hour is held back", async () => {
    const el = await render(<DecisionBlock res={answer(false, [0, 0, 0])} queue={EMPTY} />, false);
    expect(buttons(el).map(label)).toEqual(["Connect firm by the hourly study", "Decline"]);
    expect(buttons(el)[0].disabled).toBe(false);
    expect(text(el)).toContain("the hourly study connects it firm");
    expect(text(el)).not.toMatch(/envelope/i);
  });

  it("offers no firm connection where some hour would be over a limit, and says how many", async () => {
    const el = await render(<DecisionBlock res={answer(false, [0, 7, 3])} queue={EMPTY} />, false);
    const [firm, decline] = buttons(el);
    expect(label(firm)).toBe("Connect firm");
    expect(firm.disabled).toBe(true);
    expect(decline.disabled).toBe(false);
    expect(text(el)).toContain("a limit would be exceeded in 10 hours over 3 weather years");
  });

  it("connects firm by the static study when it approves, whatever the hours", async () => {
    const el = await render(<DecisionBlock res={answer(true, [0, 7, 3])} queue={EMPTY} />, false);
    expect(buttons(el).map(label)).toEqual(["Connect firm", "Decline"]);
    expect(buttons(el)[0].disabled).toBe(false);
  });

  it("keeps the envelope where the envelopes are on", async () => {
    const el = await render(<DecisionBlock res={answer(false, [0, 7, 3])} queue={EMPTY} />, true);
    expect(buttons(el).map(label)).toEqual(["Connect with this envelope", "Connect firm", "Decline"]);
  });

  it("lists a firm connection the hourly study gave as such", async () => {
    const queue: QueueState = { ...EMPTY, version: 1, baseline: "static", approved: 1,
      items: [{ assessment: "a00000000001", cid: 3, decision: "firm", basis: "hourly", decided: "2026-11-14T10:00:00+00:00" }] };
    const el = await render(<QueueList queue={queue} />, false);
    expect(text(el)).toContain("Connected firm (hourly study)");
    expect(text(el)).toContain("an approved house keeps its place");
  });
});
