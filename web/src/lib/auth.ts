import { useQueryClient } from "@tanstack/react-query";
import { API, apiWrite, useApi } from "./api";
import { useT } from "./i18n";

/** The roles sign-in grants (kapacitet/auth.py ROLES). */
export const ROLES = ["planner", "approver", "admin", "auditor"] as const;
export type Role = (typeof ROLES)[number];

/** What a guarded action is called here and in GET /auth/me (api/auth.py ACTIONS). */
export type Action =
  | "evaluate" | "check" | "decide" | "reset_queue" | "settings" | "sign_rule"
  | "issue" | "meter_check" | "market" | "export" | "applications" | "datasets" | "ocpp";

export interface Me {
  enabled: boolean;
  user: { sub: string; name: string; roles: Role[] } | null;
  /** by action: whether the person signed in may take it (every one, with sign-in off) */
  can: Partial<Record<Action, boolean>>;
  /** by action: the roles it takes, from the server's route policy */
  needs: Partial<Record<Action, Role[]>>;
  /** sign-in asked for but not configured: why, naming no value (every API call is refused) */
  /** sign-in is asked for and unusable; the reason is in the server's log, never in this reply */
  misconfigured?: boolean;
}

/** Who is signed in, if sign-in is on. Asked once; signing out reloads the page. */
export function useMe() {
  return useApi<Me>("/auth/me");
}

/**
 * Whether the person signed in may take an action, and if not, why, in their language. With
 * sign-in off, or before /auth/me answers, everything is allowed here: the server still decides.
 */
export function useCan(action: Action): { ok: boolean; reason?: string } {
  const t = useT();
  const me = useMe().data;
  if (!me?.enabled || me.can[action] !== false) return { ok: true };
  const roles = (me.needs[action] ?? []).map((r) => t(`auth.role.${r}`)).join(" / ");
  return { ok: false, reason: t("auth.needs", { roles }) };
}

/** Off to the identity provider, coming back to the screen in view. */
export function signIn() {
  const next = window.location.hash.startsWith("#/") ? window.location.hash : "";
  window.location.assign(`${API}/auth/login${next ? `?next=${encodeURIComponent(next)}` : ""}`);
}

/** End the session here, and at the provider when it says how. */
export function useSignOut() {
  const client = useQueryClient();
  return async () => {
    const r = await apiWrite("/auth/logout").catch(() => null);
    const body = r && r.ok ? await r.json().catch(() => null) : null;
    client.clear();
    if (body?.logout_url) window.location.assign(body.logout_url);
    else window.location.reload();
  };
}

/** The reason a sign-in came back without a session (`/?signin=<code>`), read once and cleared. */
export function takeSignInError(): string | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("signin");
  if (!code) return null;
  params.delete("signin");
  const rest = params.toString();
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${rest ? `?${rest}` : ""}${window.location.hash}`);
  return code;
}
