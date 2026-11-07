"""Sign-in routes (kapacitet/auth.py): who is signed in, the login and its callback, and logout.

With sign-in off, `/auth/me` says so and the login answers 404 `auth_off`; the interface then
never shows a sign-in. The guard (api/guard.py) lets these through without a session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth
from . import guard

log = logging.getLogger("kapacitet.api.auth")
router = APIRouter(prefix="/api/v1", tags=["auth"])


class RedactCallback(logging.Filter):
    """The access log writes every request's path with its query; the callback's carries the
    provider's authorization code and the login's state. Both are cut from the line (the code is
    useless without the PKCE verifier, but it has no business in a log)."""

    PATH = "/api/v1/auth/callback"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and any(isinstance(a, str) and a.startswith(self.PATH + "?") for a in args):
            record.args = tuple(self.PATH + "?[redacted]" if isinstance(a, str) and a.startswith(self.PATH + "?")
                                else a for a in args)
        elif isinstance(record.msg, str) and self.PATH + "?" in record.msg:
            record.msg = record.msg.split(self.PATH + "?")[0] + self.PATH + "?[redacted]"
        return True


logging.getLogger("uvicorn.access").addFilter(RedactCallback())

API = guard.API
# What the interface asks before it offers an action, by the route that takes it: each one's roles
# come from guard.ROUTE_POLICY, so the buttons and the guard cannot disagree.
ACTIONS = {
    "evaluate": ("POST", f"{API}/requests"),
    "check": ("POST", f"{API}/check"),
    "decide": ("POST", f"{API}/queue"),
    "reset_queue": ("DELETE", f"{API}/queue"),
    "settings": ("PUT", f"{API}/settings"),
    "sign_rule": ("PUT", f"{API}/settings/rule-signature"),
    "issue": ("POST", f"{API}/operation/schedule/issue"),
    "meter_check": ("POST", f"{API}/operation/meter-check"),
    "market": ("POST", f"{API}/operation/market-alternatives"),
    "export": ("POST", f"{API}/eipack/export"),
    "applications": ("POST", f"{API}/applications"),
    "datasets": ("POST", f"{API}/datasets"),
}

# The refusals of these routes, in the API's form (api/main.API_ERRORS takes them in).
ERRORS = {
    "auth_off": "sign-in is not turned on for this installation",
    "login_failed": "the sign-in could not be completed: {detail}",
    "provider_unavailable": "the identity provider could not be reached: {detail}",
}


def _refusal(status: int, code: str, **params) -> HTTPException:
    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


def _needs() -> dict[str, list[str]]:
    return {a: list(guard.ROUTE_POLICY[k].roles) for a, k in ACTIONS.items()}


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _config() -> auth.Config:
    try:
        cfg = auth.config()
    except auth.AuthConfigError as exc:
        raise HTTPException(503, {"code": "auth_misconfigured", "message": guard.ERRORS["auth_misconfigured"]
                                  .format(detail=str(exc)), "params": {"detail": str(exc)}}) from exc
    if cfg is None:
        raise _refusal(404, "auth_off")
    return cfg


@router.get("/auth/me")
def me(request: Request) -> dict:
    """Whether sign-in is on, who is signed in with which roles, and which actions they may take
    (`can`, by action; `needs`, the roles each action takes). Sign-in asked for but misconfigured:
    `misconfigured` says why (naming no value), so the interface can show it; nothing is allowed."""
    needs = _needs()
    try:                        # memoized: no reading .env on every request
        cfg = auth.config()
    except auth.AuthConfigError as exc:
        return {"enabled": True, "user": None, "can": {a: False for a in needs}, "needs": needs,
                "misconfigured": str(exc)}
    if cfg is None:
        return {"enabled": False, "user": None, "can": {a: True for a in needs}, "needs": needs}
    s = auth.session_from_cookie(request.headers.get("cookie"), cfg)
    user = None if s is None else {"sub": s["sub"], "name": s["name"], "roles": s["roles"]}
    can = {a: bool(user) and auth.acting_role(user["roles"], tuple(r)) is not None for a, r in needs.items()}
    return {"enabled": True, "user": user, "can": can, "needs": needs, "login": f"{API}/auth/login"}


@router.get("/auth/login")
def login(request: Request, next: str | None = Query(None, max_length=300)) -> RedirectResponse:  # noqa: A002
    """Send the browser to the identity provider (authorization code flow with PKCE)."""
    cfg = _config()
    redirect_uri = cfg.redirect_uri or str(request.base_url).rstrip("/") + f"{API}/auth/callback"
    try:
        url, tx = auth.start_login(cfg, redirect_uri, auth.safe_next(next))
    except auth.AuthError as exc:
        raise _refusal(502, "provider_unavailable", detail=str(exc)) from exc
    response = RedirectResponse(url, status_code=302)
    response.headers.append("set-cookie", auth.cookie_header(
        auth.LOGIN_COOKIE, auth.sign(tx, cfg.session_key, "login"), auth.LOGIN_TTL_S, _secure(request),
        path=f"{API}/auth"))
    response.headers["Cache-Control"] = "no-store"
    return response


def _back(request: Request, target: str, *cookies: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=302)
    for c in cookies:
        response.headers.append("set-cookie", c)
    response.headers.append("set-cookie", auth.cookie_header(auth.LOGIN_COOKIE, "", 0, _secure(request),
                                                             path=f"{API}/auth"))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/auth/callback")
def callback(request: Request, code: str | None = Query(None, max_length=4096),
             state: str | None = Query(None, max_length=512),
             error: str | None = Query(None, max_length=200)) -> RedirectResponse:
    """The provider's answer: exchange the code, verify the ID token, open the session, and go back
    to where the login started. A failure lands on the start page with `?signin=<code>`."""
    cfg = _config()
    tx = auth.unsign(auth._cookie(request.headers.get("cookie"), auth.LOGIN_COOKIE), cfg.session_key, "login")
    if error or not code or not state or tx is None:
        log.info("sign-in not completed: %s", "provider error" if error else "no login in progress")
        return _back(request, "/?signin=login_failed")
    try:
        claims = auth.finish_login(cfg, tx, code, state)
    except auth.AuthError as exc:
        log.warning("sign-in failed: %s", exc)
        return _back(request, f"/?signin={exc.code}")
    session = auth.new_session(claims, cfg)
    log.info("signed in: roles %s", ",".join(session["roles"]) or "none")
    cookie = auth.cookie_header(auth.SESSION_COOKIE, auth.sign(session, cfg.session_key, "session"),
                                cfg.session_s, _secure(request))
    return _back(request, "/" + (tx.get("next") or ""), cookie)


@router.post("/auth/logout")
def logout(request: Request) -> JSONResponse:
    """End the session here; `logout_url`, when the provider has one, ends it there too."""
    cfg = _config()
    s = auth.session_from_cookie(request.headers.get("cookie"), cfg)
    if s is not None:
        auth.revoke(s)
    after = str(request.base_url)
    response = JSONResponse({"ok": True, "logout_url": auth.logout_url(cfg, after)},
                            headers={"Cache-Control": "no-store"})
    response.headers.append("set-cookie", auth.cookie_header(auth.SESSION_COOKIE, "", 0, _secure(request)))
    return response
