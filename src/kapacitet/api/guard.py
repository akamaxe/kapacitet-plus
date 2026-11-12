"""What a request has to be before the API acts on it. One ASGI layer in front of every route
(`Guard`, registered in api/main.py), four checks in this order:

1. **Host.** The server answers only to the names it is reached by: localhost, 127.0.0.1 and
   [::1], the names in `KAPACITET_ALLOWED_HOSTS` (comma-separated; `*` turns the check off), and
   in a GitHub codespace (`CODESPACE_NAME`) the forwarded names under
   `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN`, a domain GitHub controls. A page on another site
   that rebinds its own name to this machine is answered with `bad_host`.
2. **Where a write comes from.** A POST, PUT or DELETE that names its origin must come from the
   name it was sent to, from one of those hosts (on any port, so the Vite dev server passes) or
   from `KAPACITET_CORS_ORIGINS`; `Origin: null` never does, and `*` lets no other site write. Without an Origin, a browser's `Sec-Fetch-Site` must say the request
   is same-origin or typed by the user. A request with neither header (curl, a script, the tests)
   is not a browser's, so it passes.
3. **What a write carries.** A write must say it sends JSON (`application/json`), or bytes for the
   setup page's upload. A form or `text/plain`, the only bodies a cross-site page can send without
   asking first, are refused, so every write from another site needs a preflight that the
   server never grants.
4. **Who asks.** With sign-in on (`KAPACITET_AUTH=oidc`, kapacitet/auth.py), every API request
   but the public ones (`PUBLIC`: health, the sign-in routes, the applicant's portal and the studies'
   signed publications, api/publications.py) needs a
   session: none is `auth_required` (401), a role the route does not take is `role_required` (403,
   naming the roles it does). A write needs one of its policy's `roles`, a read any role. The person
   and the role they act under are then the request's actor (kapacitet/actor.py), which the seals
   and the queue's decisions record. With sign-in off nothing here changes and there is no actor.
5. **How often.** A sliding window per client address and bucket: an application's status 10 a
   minute, an evaluation 10, any other write 30, a read of the studies' publications 120. The answer to one too many is 429 with
   `Retry-After` and the code `rate_limited`. Uvicorn takes the client from `X-Forwarded-For` only
   when the proxy is on 127.0.0.1 (a codespace's port forwarding).

`ROUTE_POLICY` lists every route that changes something, what it writes, what gates it and which
roles may call it: the inventory of the API's writes in one place. tests/test_guard.py holds it to the app's routes.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import actor, auth, telemetry

API = "/api/v1"
UNSAFE = ("POST", "PUT", "DELETE", "PATCH")
JSON = "application/json"
OCTET = "application/octet-stream"

# Refusals in the API's own form (api/main.API_ERRORS takes these in, so the interface's
# `err.<code>` and tests/test_messages.py cover them like any other).
ERRORS = {
    "bad_host": "this server does not answer to {host}",
    "bad_origin": "a write from {origin} is not accepted",
    "bad_content_type": "a write here is sent as {expected}",
    "rate_limited": "too many requests: try again in {seconds} s",
    "auth_required": "sign in to use this",
    "role_required": "this needs the role {roles}",
    "auth_misconfigured": "sign-in is turned on but not configured",      # the log says what is missing
}

# Requests a client may make per minute in each bucket; "upload" (the setup page's chunks) is not
# counted: it is open only on an empty copy that asked for it, and a bundle is dozens of chunks.
WINDOW_S = 60.0
LIMITS = {"status": 10, "compute": 10, "write": 30, "upload": None, "public": 120}


# Who may call a route when sign-in is on (kapacitet/auth.py ROLES), in the order a person holding
# several acts under: the first one they hold is the role their action records.
PLANNER_UP = ("planner", "approver", "admin")    # evaluating: planners and anyone above them
APPROVER = ("approver",)                         # deciding
ADMIN_APPROVER = ("admin", "approver")           # the DSO's settings, its rule and its market statements
ADMIN = ("admin",)                               # the installation: networks and the data bundle
ANY_ROLE = ("planner", "approver", "admin", "auditor")   # reading, and exporting evidence
PUBLIC = ()                                      # no session: the applicant's portal, behind its code


@dataclass(frozen=True)
class Policy:
    bucket: str      # which rate limit it counts against (LIMITS)
    writes: str      # what it changes; "nothing" for a computation answered in memory
    gate: str = ""   # what has to hold before it runs, besides the guard
    body: str = JSON  # the content type it has to be sent with
    roles: tuple[str, ...] = ADMIN   # who may call it with sign-in on; every entry below names its own


# Every POST, PUT and DELETE of the API, by method and route. (The studies' signed publications,
# api/publications.py, are reads: public in PUBLIC_READS and limited in LIMITED_READS.)
ROUTE_POLICY: dict[tuple[str, str], Policy] = {
    ("POST", f"{API}/check"): Policy("compute", "nothing: checks the four CSV tables in memory", roles=PLANNER_UP),
    ("POST", f"{API}/requests"): Policy(
        "compute", "data/requests/<id>.json.gz, its index, the injection cache and a seal",
        "KAPACITET_MAX_JOBS evaluations at once and KAPACITET_MAX_QUEUED waiting, each within "
        "KAPACITET_JOB_TIMEOUT_S", roles=PLANNER_UP),
    ("DELETE", f"{API}/requests/{{job_id}}"): Policy("write", "nothing: a waiting or running evaluation stops",
                                                     "only one that has not finished", roles=PLANNER_UP),
    ("POST", f"{API}/evaluate"): Policy("compute", "nothing: the evaluation is answered in the request",
                                        "the same pool as POST /requests", roles=PLANNER_UP),
    ("POST", f"{API}/queue"): Policy("write", "the queue, its seal and the application it decides",
                                     "one queue update at a time", roles=APPROVER),
    ("DELETE", f"{API}/queue"): Policy("write", "a seal, then the queue is emptied", "one queue update at a time",
                                       roles=APPROVER),
    ("PUT", f"{API}/settings"): Policy("write", "data/requests/settings.json and a seal", roles=ADMIN_APPROVER),
    ("PUT", f"{API}/settings/rule-signature"): Policy("write", "data/requests/settings.json and two seals: "
                                                               "the signature and the change", roles=ADMIN_APPROVER),
    ("PUT", f"{API}/settings/brand-logo"): Policy("write", "data/requests/brand-logo.png (or its row) and a seal "
                                                           "of its digest", roles=ADMIN_APPROVER),
    ("DELETE", f"{API}/settings/brand-logo"): Policy("write", "removes data/requests/brand-logo.png (or its row) and "
                                                              "a seal of the digest it had", roles=ADMIN_APPROVER),
    ("POST", f"{API}/operation/meter-check"): Policy("write", "nothing: the meter's file is checked in memory",
                                                     roles=PLANNER_UP),
    ("POST", f"{API}/operation/schedule/issue"): Policy(
        "write", "a seal of the schedule issued, with its market terms",
        "after the day-ahead market's result for the day (market_result_local on the day before), "
        "unless an override_reason is given", roles=ADMIN_APPROVER),
    ("POST", f"{API}/operation/market-alternatives"): Policy(
        "write", "market_alternatives.jsonl and a seal", "an Idempotency-Key sent again records nothing new",
        roles=ADMIN_APPROVER),
    ("POST", f"{API}/eipack/export"): Policy("write", "a seal of the evidence pack exported", roles=ANY_ROLE),
    ("POST", f"{API}/applications"): Policy("write", "applications.jsonl and a seal",
                                            "the rule then decides it when the DSO turned auto_decisions on and "
                                            "signed the rule for it", roles=PLANNER_UP),
    ("POST", f"{API}/applications/{{app_id}}/requeue"): Policy("write", "applications.jsonl",
                                                               "only an application in review", roles=PLANNER_UP),
    ("POST", f"{API}/apply/evaluate"): Policy("compute", "as POST /requests, against the DSO's grid", roles=PUBLIC),
    ("DELETE", f"{API}/apply/evaluate/{{job_id}}"): Policy("write", "as DELETE /requests/{id}", roles=PUBLIC),
    ("POST", f"{API}/apply/submit"): Policy("write", "as POST /applications", roles=PUBLIC),
    ("POST", f"{API}/datasets"): Policy("write", "data/datasets/<id> and its build process", roles=ADMIN),
    ("POST", f"{API}/datasets/{{ds_id}}/build"): Policy(
        "write", "a build process for data/datasets/<id>",
        "not the network in use, and at most KAPACITET_MAX_BUILDS builds at once", roles=ADMIN),
    ("POST", f"{API}/datasets/{{ds_id}}/cancel"): Policy("write", "stops the build process; its record says so",
                                                        "only a build that is running", roles=ADMIN),
    ("DELETE", f"{API}/datasets/{{ds_id}}"): Policy("write", "removes data/datasets/<id>",
                                                    "not the network in use", roles=ADMIN),
    ("POST", f"{API}/datasets/{{ds_id}}/activate"): Policy(
        "write", "data/active.json, then the server restarts on that network",
        "KAPACITET_SUPERVISED=1 (set by `kapacitet serve`) and no job running", roles=ADMIN),
    ("PUT", f"{API}/setup/upload"): Policy(
        "upload", "data/processed and data/raw, then the server restarts",
        "KAPACITET_SETUP_UPLOAD=1, the reference network and no results yet", body=OCTET, roles=ADMIN),
    ("POST", f"{API}/auth/logout"): Policy("write", "nothing: the session cookie is cleared and its id forgotten",
                                           roles=PUBLIC),
}
# Reads anyone may make with sign-in on: the server's health, the sign-in itself and the applicant's
# portal (its answers are behind the application's code). Every other read needs one of ANY_ROLE.
PUBLIC_READS = (f"{API}/health", f"{API}/auth/me", f"{API}/auth/login", f"{API}/auth/callback", f"{API}/apply/",
                f"{API}/publications", f"{API}/publications/")
# Reads that are limited too: guessing an application's code is the one read worth slowing down.
# The studies' publications are public and framed on other sites: 120 a minute per address (a page
# view of the embed is five requests).
LIMITED_READS: dict[tuple[str, str], str] = {
    ("GET", f"{API}/apply/status/{{reference}}"): "status",
    ("GET", f"{API}/publications"): "public",
    ("GET", f"{API}/publications/{{study}}/{{run}}/{{file:path}}"): "public",
}
# Reads held to fewer roles than ANY_ROLE: what a DSO hands on to devices is a planner's to fetch.
ROLE_READS: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", f"{API}/operation/schedule.ocpp.json"): PLANNER_UP,
}
# A write the table does not know (a typo, a method a route does not take) is held to the strictest rules.
DEFAULT = Policy("write", "unknown route", roles=ADMIN)


def _pattern(template: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", template)
    return re.compile("".join((".+" if p.endswith(":path}") else "[^/]+") if p.startswith("{") else re.escape(p)
                              for p in parts))


_ROUTES = [(m, _pattern(t), p) for (m, t), p in ROUTE_POLICY.items()]
_READS = [(m, _pattern(t), b) for (m, t), b in LIMITED_READS.items()]


def policy_for(method: str, path: str) -> Policy | None:
    """The policy of the route a write goes to, or None when no route of the table matches."""
    return next((p for m, rx, p in _ROUTES if m == method and rx.fullmatch(path)), None)


def roles_for(method: str, path: str) -> tuple[str, ...]:
    """The roles a request needs when sign-in is on: PUBLIC (none) for the interface's own files,
    the public reads and the portal's writes, its policy's roles for a write, any role for a read."""
    if not path.startswith(API + "/"):
        return PUBLIC
    if method in UNSAFE:
        return (policy_for(method, path) or DEFAULT).roles
    if any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC_READS):
        return PUBLIC
    return ROLE_READS.get(("GET" if method == "HEAD" else method, path), ANY_ROLE)


def bucket_for(method: str, path: str) -> str | None:
    """Which rate limit a request counts against, or None when it is not limited."""
    if method in UNSAFE:
        return (policy_for(method, path) or DEFAULT).bucket
    return next((b for m, rx, b in _READS if m == method and rx.fullmatch(path)), None)


# ---------------------------------------------------------------- hosts and origins

def allowed_hosts() -> list[str]:
    """The names this server answers to, as TrustedHostMiddleware patterns, from the environment."""
    hosts = ["localhost", "127.0.0.1", "[::1]"]
    hosts += [h.strip().lower() for h in os.environ.get("KAPACITET_ALLOWED_HOSTS", "").split(",") if h.strip()]
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").strip().lower()
    if os.environ.get("CODESPACE_NAME") and domain:
        hosts.append(f"*.{domain}")
    return hosts


def cors_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in os.environ.get("KAPACITET_CORS_ORIGINS", "").split(",") if o.strip()}


_HOST = re.compile(r"(?P<host>\[[0-9a-fA-F:.]+\]|[^:\[\]/@\s]+)(?::(?P<port>\d{1,5}))?")


def host_of(header: str | None) -> str | None:
    """The name in a Host header, lower case and without its port; None when it is not one."""
    m = _HOST.fullmatch((header or "").strip())
    return m["host"].lower() if m else None


def matches(host: str, patterns: list[str]) -> bool:
    """TrustedHostMiddleware's rule: the name itself, or under a `*.domain` pattern."""
    return any(host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in patterns)


def origin_ok(origin: str, own_host: str | None, patterns: list[str], cors: set[str]) -> bool:
    """Whether a write may come from `origin`: the name the request was sent to (`own_host`, which
    has passed the host check), an allowed host on any port, or an origin listed for CORS."""
    if origin.rstrip("/") in cors:
        return True
    if origin == "null":
        return False
    try:
        parts = urlsplit(origin)
        name = parts.hostname
        parts.port                           # noqa: B018 - a port that is not a number raises here
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not name:
        return False
    name = f"[{name}]" if ":" in name else name
    return name == own_host or matches(name, patterns)


# ---------------------------------------------------------------- the rate limit

class Limiter:
    """A sliding window per (bucket, client): the requests it let through in the last WINDOW_S."""

    MAX_CLIENTS = 10_000

    def __init__(self, limits: dict[str, int | None] = LIMITS, window_s: float = WINDOW_S, clock=time.monotonic):
        self.limits, self.window_s, self.clock = limits, window_s, clock
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, client: str) -> int | None:
        """Count a request; None when it may go ahead, else the seconds until it may."""
        limit = self.limits.get(bucket)
        if limit is None:
            return None
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault((bucket, client), deque())
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= limit:
                return max(1, math.ceil(q[0] + self.window_s - now))
            q.append(now)
            if len(self._hits) > self.MAX_CLIENTS:
                self._prune(now)
        return None

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window_s]:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = Limiter()


# ---------------------------------------------------------------- the layer

def refusal(status: int, code: str, headers: dict | None = None, **params) -> JSONResponse:
    """The same body an HTTPException from api_error has, so the interface reads it the same way."""
    telemetry.refused(code, status)       # counted when telemetry is on, by code only
    return JSONResponse({"detail": {"code": code, "message": ERRORS[code].format(**params), "params": params}},
                        status_code=status, headers=headers)


ACTOR_KEY = "kapacitet.actor"     # where check() leaves the request's actor in its scope


def signed_in(scope: Scope, headers: Headers, method: str, path: str) -> JSONResponse | None:
    """With sign-in on: refuse a request without the session or the role its route needs, and
    leave the person and the role they act under in the scope (ACTOR_KEY). Off: nothing."""
    scope.pop(ACTOR_KEY, None)
    # the interface's own files and /auth/me answer even when sign-in is misconfigured, so the
    # interface loads and says what is wrong in the reader's language; every other API path fails closed
    if not path.startswith(API + "/") or path == f"{API}/auth/me":
        return None
    try:
        cfg = auth.config()
    except auth.AuthConfigError as exc:      # fail closed: sign-in was asked for
        auth.report_misconfigured(exc)
        return refusal(503, "auth_misconfigured")
    if cfg is None:
        return None
    allowed = roles_for(method, path)
    if not allowed:
        return None
    session = auth.session_from_cookie(headers.get("cookie"), cfg)
    if session is None:
        return refusal(401, "auth_required")
    role = auth.acting_role(session["roles"], allowed)
    if role is None:
        return refusal(403, "role_required", roles=", ".join(allowed))
    scope[ACTOR_KEY] = {"sub": session["sub"], "name": session["name"], "role": role}
    return None


class Guard(TrustedHostMiddleware):
    """Starlette's TrustedHostMiddleware, answering in the API's refusal form, with the checks on
    writes and the rate limit behind it. The environment is read once, when the app is built."""

    def __init__(self, app: ASGIApp, allowed: list[str] | None = None, limiter: Limiter | None = None) -> None:
        super().__init__(app, allowed_hosts=allowed if allowed is not None else allowed_hosts(), www_redirect=False)
        self.cors = cors_origins()
        self.limiter = limiter or LIMITER

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":          # the lifespan; the API serves no websockets
            await self.app(scope, receive, send)
            return
        response = self.check(scope)
        if response is None:
            with actor.acting_as(scope.get(ACTOR_KEY)):
                await self.app(scope, receive, send)
        else:
            await response(scope, receive, send)

    def check(self, scope: Scope) -> JSONResponse | None:
        headers = Headers(scope=scope)
        host = host_of(headers.get("host"))
        if not self.allow_any and (host is None or not matches(host, self.allowed_hosts)):
            return refusal(400, "bad_host", host=(headers.get("host") or "")[:100])
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method in UNSAFE:
            origin = headers.get("origin")
            if origin is not None:
                if not origin_ok(origin, host, self.allowed_hosts, self.cors):
                    return refusal(403, "bad_origin", origin=origin[:100])
            elif headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return refusal(403, "bad_origin", origin=headers.get("sec-fetch-site", "")[:40])
            expected = (policy_for(method, path) or DEFAULT).body
            if headers.get("content-type", "").split(";")[0].strip().lower() != expected:
                return refusal(415, "bad_content_type", expected=expected)
        refused = signed_in(scope, headers, method, path)
        if refused is not None:
            return refused
        bucket = bucket_for(method, path)
        if bucket is not None:
            client = (scope.get("client") or ("unknown", 0))[0]
            wait = self.limiter.hit(bucket, client)
            if wait is not None:
                return refusal(429, "rate_limited", headers={"Retry-After": str(wait)}, seconds=wait)
        return None
