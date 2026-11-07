import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GROUPS, SettingsView, type SettingsReply } from "./DsoSettings";

// Operation's Settings section: every DSO setting with its value, the six with no other screen
// changed here a group at a time through PUT /settings. The values, bounds and units below stand in
// for the settings reply; the component types none of them.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const pct = { kind: "number", nullable: false, min: 0, max: 100, unit: "%", step: 0.1 } as const;
const reply: SettingsReply = {
  settings: {
    allocation_policy: "maxmin", agreed_level_pct: 3, auto_decisions: false,
    auto_curtailment_mean_pct: null, auto_curtailment_p90_pct: 2, auto_ev_unserved_pct: 1,
    inbox_baseline: "static", deferral_years: 6, market_alternative_statement: "", method_approval_reference: "",
    review_statement: "Every year.", market_result_local: "13:00", redispatch_compensation_sek: 0,
    import_window_days: 14, brand_name: "Elnät Söder AB", brand_contact: "", brand_accent: "", rule_signature: null,
  },
  schema: {
    allocation_policy: { kind: "choice", nullable: false, default: "maxmin", options: ["maxmin", "prorata", "propfair", "maxtotal"] },
    agreed_level_pct: { ...pct, default: 3 },
    auto_decisions: { kind: "boolean", nullable: false, default: false },
    auto_curtailment_mean_pct: { ...pct, nullable: true, default: null },
    auto_curtailment_p90_pct: { ...pct, nullable: true, default: null },
    auto_ev_unserved_pct: { ...pct, default: 1 },
    inbox_baseline: { kind: "choice", nullable: false, default: "static", options: ["static", "doe"] },
    deferral_years: { kind: "integer", nullable: false, default: 4, min: 1, max: 40, unit: "years", step: 1 },
    market_alternative_statement: { kind: "text", nullable: false, default: "", max_length: 2000 },
    method_approval_reference: { kind: "text", nullable: false, default: "", max_length: 200 },
    review_statement: { kind: "text", nullable: false, default: "", max_length: 2000 },
    market_result_local: { kind: "time", nullable: false, default: "13:00", pattern: "^([01]\\d|2[0-3]):[0-5]\\d$" },
    redispatch_compensation_sek: { kind: "number", nullable: false, default: 0, min: 0, max: 1e12, unit: "SEK", step: 1 },
    import_window_days: { kind: "integer", nullable: false, default: 14, min: 0, max: 91, unit: "days", step: 1 },
    brand_name: { kind: "text", nullable: false, default: "", max_length: 80 },
    brand_contact: { kind: "text", nullable: false, default: "", max_length: 200 },
    brand_accent: { kind: "text", nullable: false, default: "", pattern: "^(#[0-9a-f]{6})?$" },
    rule_signature: { kind: "object", nullable: true, default: null },
  },
  changed: { at: "2026-11-06T10:00:00+00:00", role: "admin" },
};

let root: Root | null = null;
let host: HTMLElement | null = null;
let calls: [string, RequestInit][] = [];

/** Render the section with a fetch that answers GET /auth/me with `me` and a write with `write`. */
async function render(opts: { me?: object; write?: () => Response } = {}): Promise<HTMLElement> {
  calls = [];
  const me = opts.me ?? { enabled: false, user: null, can: {}, needs: {} };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method) {
      calls.push([url, init]);
      return opts.write ? opts.write() : new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(url.endsWith("/auth/me") ? me : {}), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}><SettingsView reply={reply} /></QueryClientProvider>);
  });
  await settle();
  return host;
}

async function settle(until: () => boolean = () => false) {
  for (let i = 0; i < 20 && !until(); i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}

function type(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}

const text = (el: Element) => (el.textContent ?? "").replace(/\s+/g, " ");
const group = (id: string) => document.querySelector(`[aria-labelledby="set-${id}"]`) as HTMLElement;
const button = (g: HTMLElement, label: string) => [...g.querySelectorAll("button")].find((b) => b.textContent?.startsWith(label)) as HTMLButtonElement;
const field = (key: string) => document.getElementById(`set-f-${key}`) as HTMLInputElement;

describe("the DSO's settings", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    document.body.innerHTML = "";
    root = null;
    vi.unstubAllGlobals();
  });

  it("lists every setting in one group, with its value, unit and default when it differs", async () => {
    const keys = GROUPS.flatMap((g) => g.rows.map((r) => r.key));
    expect(new Set(keys).size).toBe(keys.length);
    expect(new Set(keys)).toEqual(new Set(Object.keys(reply.schema)));

    const el = await render();
    expect(text(el)).toContain("as Admin");
    // the six without another screen have controls, filled with the values in force
    expect(field("agreed_level_pct").value).toBe("3");
    expect(field("deferral_years").value).toBe("6");
    expect(field("market_result_local").value).toBe("13:00");
    expect((field("inbox_baseline") as unknown as HTMLSelectElement).value).toBe("static");
    expect(field("auto_curtailment_mean_pct").value).toBe("");
    // the bounds come from the reply; a guard-only bound (the compensation's) is not printed
    expect(text(group("envelopes"))).toContain("(1–40)");
    expect(text(group("market"))).not.toContain("1 000 000");
    // a value that is not the default says the default
    expect(text(group("envelopes"))).toContain("Default: 4 years");
    expect(text(group("rule"))).toContain("Default: Follows the agreed level (3.0 %)");
    // the rest show their value and where they are changed
    expect(text(group("envelopes"))).toContain("Equal kW for everyone");
    expect(text(group("envelopes"))).toContain("Change it under “Annual report › Sharing rule”");
    expect(text(group("envelopes"))).toContain("14 days");
    expect(text(group("rule"))).toContain("Not signed");
    expect(text(group("portal"))).toContain("Elnät Söder AB");
    expect(text(group("ei"))).toContain("Not stated");
    expect(text(group("ei"))).toContain("Every year.");
    // groups with nothing to change here have no save button
    expect(button(group("portal"), "Save")).toBeUndefined();
  });

  it("saves a group with only the fields that changed, and says what a threshold change affects", async () => {
    const el = await render();
    const rule = group("rule");
    expect(button(rule, "Save").disabled).toBe(true);
    await act(async () => type(field("auto_curtailment_p90_pct"), "2.5"));
    await act(async () => type(field("inbox_baseline"), "doe"));
    expect(text(rule)).toContain("The rule's next proposals use the new thresholds");
    expect(button(rule, "Save").disabled).toBe(false);
    await act(async () => button(rule, "Save").click());
    await settle(() => calls.length > 0);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("/api/v1/settings");
    expect(calls[0][1].method).toBe("PUT");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ auto_curtailment_p90_pct: 2.5, inbox_baseline: "doe" });
    expect(el.querySelector("[role=alert]")).toBeNull();
  });

  it("sends an emptied threshold as null, and refuses a value past the API's bound", async () => {
    await render();
    const env = group("envelopes");
    await act(async () => type(field("deferral_years"), "41"));
    expect(button(env, "Save").disabled).toBe(true);
    await act(async () => type(field("deferral_years"), "8"));
    await act(async () => type(field("agreed_level_pct"), "4"));
    expect(text(env)).toContain("The agreed level is what each house is held to");
    await act(async () => button(env, "Save").click());
    await settle(() => calls.length > 0);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ deferral_years: 8, agreed_level_pct: 4 });

    calls.length = 0;
    const rule = group("rule");
    await act(async () => type(field("auto_curtailment_p90_pct"), ""));
    await act(async () => button(rule, "Save").click());
    await settle(() => calls.length > 0);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ auto_curtailment_p90_pct: null });
  });

  it("shows a refusal in its own words", async () => {
    const refusal = { detail: { code: "role_required", message: "this needs the role admin, approver", params: { roles: "admin, approver" } } };
    await render({ write: () => new Response(JSON.stringify(refusal), { status: 403, headers: { "Content-Type": "application/json" } }) });
    const market = group("market");
    await act(async () => type(field("market_result_local"), "12:30"));
    await act(async () => button(market, "Save").click());
    await settle(() => !!market.querySelector("[role=alert]"));
    expect(calls).toHaveLength(1);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ market_result_local: "12:30" });
    expect(market.querySelector("[role=alert]")!.textContent).toContain("admin, approver");
  });

  it("shows a validation refusal from the server as its translated text", async () => {
    const refusal = { detail: { code: "brand_accent_format", message: "bad accent", params: {} } };
    await render({ write: () => new Response(JSON.stringify(refusal), { status: 422, headers: { "Content-Type": "application/json" } }) });
    const market = group("market");
    await act(async () => type(field("market_result_local"), "12:30"));
    await act(async () => button(market, "Save").click());
    await settle(() => !!market.querySelector("[role=alert]"));
    expect(market.querySelector("[role=alert]")!.textContent).toContain("colour #RRGGBB");
  });

  it("disables every control without a role that may change settings", async () => {
    const me = { enabled: true, user: { sub: "s", name: "Ada", roles: ["planner"] }, can: { settings: false }, needs: { settings: ["approver", "admin"] } };
    const el = await render({ me });
    await settle(() => field("agreed_level_pct").disabled);
    for (const key of ["agreed_level_pct", "deferral_years", "market_result_local", "inbox_baseline", "auto_ev_unserved_pct", "redispatch_compensation_sek"]) {
      expect(field(key).disabled, key).toBe(true);
    }
    expect(text(el)).toContain("Needs the role Approver / Admin.");
    await act(async () => type(field("deferral_years"), "8"));
    expect(button(group("envelopes"), "Save").disabled).toBe(true);
  });
});
