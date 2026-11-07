import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RoleNote } from "./RoleNote";
import { takeSignInError, type Me } from "../lib/auth";

// With sign-in on, an action the person's roles do not cover is shown disabled with the roles it
// takes (GET /auth/me says both); with sign-in off nothing is said and everything stays allowed.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(me: Me): Promise<HTMLElement> {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(me), { status: 200, headers: { "Content-Type": "application/json" } })));
  host = document.createElement("div");
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(<QueryClientProvider client={client}><RoleNote action="decide" /><RoleNote action="evaluate" /></QueryClientProvider>);
  });
  for (let i = 0; i < 20 && !host.textContent; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  return host;
}

const needs: Me["needs"] = { decide: ["approver"], evaluate: ["planner", "approver", "admin"] };

describe("actions a role cannot take", () => {
  afterEach(() => {
    act(() => root?.unmount());
    host?.remove();
    root = null;
    vi.unstubAllGlobals();
  });

  it("names the roles an action takes when the person signed in holds none of them", async () => {
    const el = await render({ enabled: true, user: { sub: "s", name: "Ada", roles: ["planner"] }, can: { decide: false, evaluate: true }, needs });
    expect(el.textContent).toBe("Needs the role Approver.");
  });

  it("says nothing with sign-in off", async () => {
    const el = await render({ enabled: false, user: null, can: { decide: true, evaluate: true }, needs });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(el.textContent).toBe("");
  });

  it("reads a failed sign-in's reason from the address once, and leaves the hash alone", () => {
    window.history.replaceState(null, "", "/?signin=login_failed#/request?cid=4");
    expect(takeSignInError()).toBe("login_failed");
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#/request?cid=4");
    expect(takeSignInError()).toBeNull();
  });
});
