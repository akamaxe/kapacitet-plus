import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ImportBlock, type ImportStatus } from "./ImportEnvelopes";

// The EV chargers' import envelopes in Operation: the window they were issued with against the one
// the DSO's settings hold, the audit of the hours over the planning ceiling, and the setting itself,
// changed through PUT /settings. Every figure comes from the status block; none is typed here.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const COMMAND = "kapacitet compute --stage extra --only operation";
const fresh: ImportStatus = {
  computed: true, window_days: 14, setting_window_days: 14, max_window_days: 91, stale: false,
  actual_violation_hours: 0,
  exceedance: { hours_over: 89, customer_hours_over: 135, worst_customer_kw: 0.6659, aggregate_hours_over: 3, worst_aggregate_kw: 0.2 },
  command: COMMAND,
};

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(status: ImportStatus): Promise<HTMLElement> {
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}><ImportBlock status={status} /></QueryClientProvider>);
  });
  return host;
}

/** Type into a controlled input the way React listens for it. */
function type(el: HTMLInputElement, value: string) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

const text = (el: HTMLElement) => (el.textContent ?? "").replace(/\s+/g, " ");
const save = () => [...document.querySelectorAll("button")].find((b) => b.textContent === "Save the window") as HTMLButtonElement;

describe("the import envelopes' window", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    document.body.innerHTML = "";
    root = null;
    vi.unstubAllGlobals();
  });

  it("shows current envelopes with their audit and no stale notice", async () => {
    const el = await render(fresh);
    expect(text(el)).toContain("Current");
    expect(el.querySelector(".stale-note")).toBeNull();
    expect(text(el)).toContain("Hours over the planning ceiling");
    expect(text(el)).toContain("89");
    expect(text(el)).toContain("135 house-hours");
    expect(text(el)).toContain("0.67 kW");
    expect(text(el)).toContain("3 h with the houses together over");
    expect(text(el)).toContain("Hours above a grid limit");
    expect(text(el)).toContain("(0–91)");
    // nothing to save until the window changes
    expect(save().disabled).toBe(true);
  });

  it("says stale envelopes are refused and names the command that issues them again", async () => {
    const el = await render({ ...fresh, setting_window_days: 0, stale: true });
    expect(text(el)).toContain("Stale");
    const note = el.querySelector(".stale-note")!;
    expect(note).not.toBeNull();
    expect(text(note as HTMLElement)).toContain("issued with a 14-day window and the settings now hold 0 days");
    expect(text(note as HTMLElement)).toContain("refused");
    expect(note.querySelector("code")!.textContent).toBe(COMMAND);
  });

  it("says no import limits have been issued before the operation stage has run", async () => {
    const el = await render({ computed: false, window_days: null, setting_window_days: 14, max_window_days: 91, stale: false, command: COMMAND });
    expect(text(el)).toContain("Not issued");
    expect(text(el)).toContain("No import limits have been issued yet");
    expect(el.querySelector("code")!.textContent).toBe(COMMAND);
    expect(el.querySelector(".stale-note")).toBeNull();
    expect(text(el)).not.toContain("Hours over the planning ceiling");
  });

  it("sends the new window to PUT /settings, and refuses one past the maximum", async () => {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      // the writes only: the controls also ask who is signed in (GET /auth/me)
      if (init?.method) calls.push([url, init]);
      return new Response(JSON.stringify({ settings: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const el = await render(fresh);
    const input = el.querySelector("input[type=number]") as HTMLInputElement;
    await act(async () => type(input, "92"));
    expect(save().disabled).toBe(true);
    await act(async () => type(input, "7"));
    expect(save().disabled).toBe(false);
    await act(async () => save().click());
    for (let i = 0; i < 20 && calls.length === 0; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("/api/v1/settings");
    expect(calls[0][1].method).toBe("PUT");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ import_window_days: 7 });
  });
});
