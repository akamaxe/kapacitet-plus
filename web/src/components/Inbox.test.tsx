import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RuleBlock, type Inbox } from "./Inbox";

// Advisor mode is the default: the rule only proposes until it is switched on and a person signs
// it. The block says which mode is in force and signs the rule through PUT /settings/rule-signature.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const thresholds = { curtailment_mean_pct: 3, curtailment_p90_pct: 3, ev_unserved_pct: 1 };
const base: Inbox = { items: [], working: null, auto_decisions: false, thresholds, rule_signature: null, signature_state: "none" };
const signature = {
  signed_by: "A. Planner", role: "network planning manager", signed_at: "2026-11-06T10:00:00+00:00",
  request_kinds: ["pv" as const, "ev" as const], max_pv_kwp: 10, thresholds, valid_until: "2099-06-30",
};

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(inbox: Inbox): Promise<HTMLElement> {
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}><RuleBlock inbox={inbox} /></QueryClientProvider>);
  });
  return host;
}

/** Type into a controlled input the way React listens for it. */
function type(el: HTMLInputElement, value: string) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("the DSO's rule", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    document.body.innerHTML = "";
    root = null;
    vi.unstubAllGlobals();
  });

  it("says the rule only proposes by default, and that nobody has signed it", async () => {
    const el = await render(base);
    expect(el.textContent).toContain("Advisor mode");
    expect(el.textContent).toContain("The rule only proposes: a person decides every application.");
    expect(el.textContent).toContain("Nobody has signed the rule");
    // switched on but unsigned is still advisor mode
    act(() => root?.unmount());
    host?.remove();
    const on = await render({ ...base, auto_decisions: true });
    expect(on.textContent).toContain("The rule only proposes");
  });

  it("names who signed, for what and until when, once a signature is in force", async () => {
    const el = await render({ ...base, auto_decisions: true, rule_signature: signature, signature_state: "in_force" });
    expect(el.textContent).toContain("Decides what is signed");
    expect(el.textContent).toContain("A. Planner (network planning manager)");
    expect(el.textContent).toMatch(/solar and EV chargers up to 10\s?kWp/);
    // a signature that ran out leaves the rule proposing again
    act(() => root?.unmount());
    host?.remove();
    const old = await render({ ...base, auto_decisions: true, rule_signature: signature, signature_state: "expired" });
    expect(old.textContent).toContain("ran out");
    expect(old.textContent).toContain("The rule only proposes");
  });

  it("signs the rule with the signer, role, scope and expiry", async () => {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      // the writes only: the buttons also ask who is signed in (GET /auth/me)
      if (init?.method) calls.push([url, init]);
      return new Response(JSON.stringify({ settings: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    await render(base);
    const open = [...document.querySelectorAll("button")].find((b) => b.textContent === "Sign the rule…")!;
    await act(async () => open.click());
    const form = document.querySelector("form.sheet") as HTMLFormElement;
    expect(form).not.toBeNull();
    const submit = form.querySelector("button[type=submit]") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    await act(async () => {
      type(form.querySelector("#sign-by")!, "B. Engineer");
      type(form.querySelector("#sign-role")!, "head of grid planning");
      type(form.querySelector("input[type=number]")!, "12");
      type(form.querySelector("input[type=date]")!, "2099-12-31");
    });
    const ev = [...form.querySelectorAll("label")].find((l) => l.textContent === "EV chargers")!.querySelector("input")!;
    await act(async () => ev.click());
    expect(submit.disabled).toBe(false);
    await act(async () => submit.click());
    for (let i = 0; i < 20 && calls.length === 0; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("/api/v1/settings/rule-signature");
    expect(calls[0][1].method).toBe("PUT");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({
      signed_by: "B. Engineer", role: "head of grid planning", request_kinds: ["pv", "ev"], max_pv_kwp: 12, valid_until: "2099-12-31",
    });
  });
});
