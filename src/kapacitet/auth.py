"""Sign-in with the DSO's identity provider (OpenID Connect), and the roles it grants.

Off by default: the demo, a codespace and CI run without it, and every route answers as before.
`KAPACITET_AUTH=oidc` turns it on, with these read from the environment or `.env` (never logged,
never written anywhere; a missing one is named, its value never):

- `KAPACITET_OIDC_ISSUER`: the provider's issuer URL (Entra ID: https://login.microsoftonline.com/<tenant>/v2.0);
- `KAPACITET_OIDC_CLIENT_ID` and `KAPACITET_OIDC_CLIENT_SECRET`: the app registration;
- `KAPACITET_SESSION_SECRET`: at least 32 characters, the key that signs the session cookie;
- optional: `KAPACITET_OIDC_ROLE_CLAIM` (the claim the roles arrive in, `roles` by default, as
  Entra ID sends app roles), `KAPACITET_OIDC_ROLE_MAP` (`ProviderValue=role,...`, or a JSON object;
  without it the claim's values are the role names themselves), `KAPACITET_OIDC_SCOPES`
  (`openid profile email`), `KAPACITET_OIDC_REDIRECT_URI` (else `<the URL the browser used>/api/v1/auth/callback`)
  and `KAPACITET_SESSION_HOURS` (8).

The flow is the authorization code flow with PKCE (S256), a state and a nonce, all three kept in a
short-lived signed cookie between the login and the callback. The ID token's signature is checked
against the provider's JWKS, with its issuer, audience, expiry and nonce. The session is a cookie
signed with HMAC-SHA256 (HttpOnly, SameSite=Lax, Secure when the browser came over https) holding
the subject, the display name and the roles; logging out clears it and forgets its id here.

Roles: planner (evaluates requests), approver (decides them), admin (configures the installation)
and auditor (reads everything, changes nothing). Which role each route needs is in
api/guard.ROUTE_POLICY.

`authlib` gives PKCE and the tokens; `joserfc` (authlib's JOSE library) checks the ID token;
`httpx` fetches the provider's documents. The helpers here are pure, so the guard can use them on
every request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from .cache import memo

log = logging.getLogger("kapacitet.auth")

ROLES = ("planner", "approver", "admin", "auditor")
SESSION_COOKIE = "kapacitet_session"
LOGIN_COOKIE = "kapacitet_login"
LOGIN_TTL_S = 600
LEEWAY_S = 60
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384")
_REQUIRED = ("KAPACITET_OIDC_ISSUER", "KAPACITET_OIDC_CLIENT_ID", "KAPACITET_OIDC_CLIENT_SECRET",
             "KAPACITET_SESSION_SECRET")


class AuthConfigError(ValueError):
    """Sign-in is asked for but cannot run; the message names variables, never their values."""


class AuthError(ValueError):
    """A login that cannot complete: `code` is what the interface says about it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Config:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    session_key: bytes = field(repr=False)
    role_claim: str = "roles"
    role_map: dict = field(default_factory=dict)
    scopes: str = "openid profile email"
    redirect_uri: str | None = None
    session_s: int = 8 * 3600


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    """Whether sign-in is asked for (`KAPACITET_AUTH=oidc`)."""
    _dotenv()
    mode = _env("KAPACITET_AUTH").lower()
    if mode in ("", "off", "none", "0", "false"):
        return False
    if mode != "oidc":
        raise AuthConfigError("KAPACITET_AUTH takes 'oidc' or 'off'")
    return True


def _dotenv() -> None:
    from .config import load_dotenv
    load_dotenv()


def parse_role_map(text: str) -> dict[str, str]:
    """`Value=role,Value2=role2` or a JSON object, to {claim value: role}; unknown roles are refused."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            pairs = json.loads(text)
        except ValueError as exc:
            raise AuthConfigError("KAPACITET_OIDC_ROLE_MAP is not valid JSON") from exc
        if not isinstance(pairs, dict):
            raise AuthConfigError("KAPACITET_OIDC_ROLE_MAP must be an object")
        items = [(str(k), str(v)) for k, v in pairs.items()]
    else:
        items = []
        for part in text.split(","):
            if not part.strip():
                continue
            if "=" not in part:
                raise AuthConfigError("KAPACITET_OIDC_ROLE_MAP takes Value=role pairs separated by commas")
            k, v = part.split("=", 1)
            items.append((k.strip(), v.strip()))
    bad = sorted({v for _, v in items if v not in ROLES})
    if bad:
        raise AuthConfigError(f"KAPACITET_OIDC_ROLE_MAP maps to roles that do not exist: {', '.join(bad)} "
                              f"(roles: {', '.join(ROLES)})")
    return {k: v for k, v in items if k}


@memo(maxsize=1)
def config() -> Config | None:
    """The sign-in settings, or None when sign-in is off. Raises AuthConfigError when it is on and
    something is missing. Read once; `config.cache_clear()` reads the environment again."""
    if not enabled():
        return None
    missing = [n for n in _REQUIRED if not _env(n)]
    if missing:
        raise AuthConfigError(f"KAPACITET_AUTH=oidc needs {', '.join(missing)}")
    secret = _env("KAPACITET_SESSION_SECRET")
    if len(secret) < 32:
        raise AuthConfigError("KAPACITET_SESSION_SECRET must be at least 32 characters")
    try:
        hours = float(_env("KAPACITET_SESSION_HOURS", "8"))
    except ValueError as exc:
        raise AuthConfigError("KAPACITET_SESSION_HOURS must be a number") from exc
    issuer = _env("KAPACITET_OIDC_ISSUER").rstrip("/")
    if not re.match(r"^https?://", issuer):
        raise AuthConfigError("KAPACITET_OIDC_ISSUER must be an http(s) URL")
    return Config(
        issuer=issuer, client_id=_env("KAPACITET_OIDC_CLIENT_ID"), client_secret=_env("KAPACITET_OIDC_CLIENT_SECRET"),
        session_key=hashlib.sha256(b"kapacitet-session/1\x00" + secret.encode()).digest(),
        role_claim=_env("KAPACITET_OIDC_ROLE_CLAIM", "roles") or "roles",
        role_map=parse_role_map(_env("KAPACITET_OIDC_ROLE_MAP")),
        scopes=_env("KAPACITET_OIDC_SCOPES", "openid profile email") or "openid profile email",
        redirect_uri=_env("KAPACITET_OIDC_REDIRECT_URI") or None,
        session_s=max(300, int(hours * 3600)),
    )


# ---------------------------------------------------------------- roles

def roles_from_claims(claims: dict, cfg: Config) -> list[str]:
    """The roles a token grants, in ROLES order: the claim's values through the role map (or as
    they are, without a map). A value the map does not know grants nothing."""
    raw = claims.get(cfg.role_claim)
    values = raw.split() if isinstance(raw, str) else [v for v in raw if isinstance(v, str)] \
        if isinstance(raw, list) else []
    got = {cfg.role_map.get(v) for v in values} if cfg.role_map else set(values)
    return [r for r in ROLES if r in got]


def acting_role(roles: list[str], allowed: tuple[str, ...]) -> str | None:
    """The role an action is taken under: the first of `allowed` the person holds."""
    return next((r for r in allowed if r in roles), None)


# ---------------------------------------------------------------- signed cookies

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, key: bytes, purpose: str) -> str:
    """`v1.<payload>.<mac>`: the JSON payload and an HMAC-SHA256 over the purpose and it."""
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(key, f"{purpose}.v1.{body}".encode(), hashlib.sha256).digest()
    return f"v1.{body}.{_b64(mac)}"


def unsign(value: str | None, key: bytes, purpose: str, now: float | None = None) -> dict | None:
    """The payload of a cookie `sign` made for `purpose`, or None when it is forged, for another
    purpose, malformed or past its `exp`."""
    if not value or value.count(".") != 2:
        return None
    ver, body, mac = value.split(".")
    if ver != "v1":
        return None
    want = hmac.new(key, f"{purpose}.v1.{body}".encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), (int, float)):
        return None
    if payload["exp"] < (time.time() if now is None else now):
        return None
    return payload


# Sessions ended by a logout, by id until they would have expired anyway: a cookie copied before
# the logout is refused. In memory, so a restart forgets them (and the session lifetime bounds it).
_REVOKED: dict[str, float] = {}
_REVOKED_LOCK = threading.Lock()


def revoke(session: dict) -> None:
    with _REVOKED_LOCK:
        now = time.time()
        for sid in [s for s, exp in _REVOKED.items() if exp < now]:
            del _REVOKED[sid]
        if isinstance(session.get("sid"), str):
            _REVOKED[session["sid"]] = float(session.get("exp", now))


def new_session(claims: dict, cfg: Config, now: float | None = None) -> dict:
    """The session a verified ID token opens: subject, display name, roles and lifetime."""
    now = time.time() if now is None else now
    name = next((claims[k] for k in ("name", "preferred_username", "email")
                 if isinstance(claims.get(k), str) and claims[k].strip()), claims["sub"])
    return {"sub": str(claims["sub"])[:200], "name": str(name).strip()[:120], "roles": roles_from_claims(claims, cfg),
            "iat": int(now), "exp": int(now + cfg.session_s), "sid": secrets.token_urlsafe(16)}


def session_from_cookie(cookie_header: str | None, cfg: Config) -> dict | None:
    """The signed-in person behind a request's Cookie header, or None."""
    value = _cookie(cookie_header, SESSION_COOKIE)
    s = unsign(value, cfg.session_key, "session")
    if s is None or not isinstance(s.get("sub"), str) or not isinstance(s.get("roles"), list):
        return None
    with _REVOKED_LOCK:
        if s.get("sid") in _REVOKED:
            return None
    s["roles"] = [r for r in ROLES if r in s["roles"]]
    return s


def _cookie(header: str | None, name: str) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip().strip('"')
    return None


def cookie_header(name: str, value: str, max_age: int, secure: bool, path: str = "/") -> str:
    """A Set-Cookie value: HttpOnly and SameSite=Lax always, Secure over https; max_age 0 deletes."""
    parts = [f"{name}={value}", f"Path={path}", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ---------------------------------------------------------------- the provider

_HTTP_TRANSPORT = None          # tests put an in-process provider here (an httpx transport)
_DOCS: dict[str, tuple[float, dict]] = {}
_DOCS_LOCK = threading.Lock()
DOC_TTL_S = 3600.0


def _client():
    import httpx
    return httpx.Client(timeout=10.0, transport=_HTTP_TRANSPORT, follow_redirects=False)


def _get_json(url: str, refresh: bool = False) -> dict:
    with _DOCS_LOCK:
        hit = _DOCS.get(url)
        if hit and not refresh and time.time() - hit[0] < DOC_TTL_S:
            return hit[1]
    with _client() as c:
        r = c.get(url, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise AuthError("provider_unavailable", f"the identity provider answered {r.status_code}")
    doc = r.json()
    if not isinstance(doc, dict):
        raise AuthError("provider_unavailable", "the identity provider sent no JSON object")
    with _DOCS_LOCK:
        _DOCS[url] = (time.time(), doc)
    return doc


def forget_provider() -> None:
    with _DOCS_LOCK:
        _DOCS.clear()


def discovery(cfg: Config) -> dict:
    """The provider's OpenID configuration; its issuer must be the one configured."""
    doc = _get_json(cfg.issuer + "/.well-known/openid-configuration")
    if str(doc.get("issuer", "")).rstrip("/") != cfg.issuer:
        raise AuthError("provider_unavailable", "the provider's issuer is not KAPACITET_OIDC_ISSUER")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(k), str):
            raise AuthError("provider_unavailable", f"the provider's configuration has no {k}")
    return doc


def start_login(cfg: Config, redirect_uri: str, after: str) -> tuple[str, dict]:
    """The provider's authorization URL and the transaction to keep until the callback."""
    from authlib.common.security import generate_token
    from authlib.common.urls import add_params_to_uri
    from authlib.oauth2.rfc7636 import create_s256_code_challenge

    doc = discovery(cfg)
    tx = {"state": generate_token(32), "nonce": generate_token(32), "verifier": generate_token(64),
          "redirect_uri": redirect_uri, "next": after, "exp": int(time.time() + LOGIN_TTL_S)}
    url = add_params_to_uri(doc["authorization_endpoint"], [
        ("response_type", "code"), ("client_id", cfg.client_id), ("redirect_uri", redirect_uri),
        ("scope", cfg.scopes), ("state", tx["state"]), ("nonce", tx["nonce"]),
        ("code_challenge", create_s256_code_challenge(tx["verifier"])), ("code_challenge_method", "S256"),
    ])
    return url, tx


def finish_login(cfg: Config, tx: dict, code: str, state: str) -> dict:
    """Exchange the code (with the PKCE verifier) and verify the ID token; returns its claims."""
    if not hmac.compare_digest(str(tx.get("state", "")), state or ""):
        raise AuthError("login_failed", "the login's state does not match")
    doc = discovery(cfg)
    with _client() as c:
        r = c.post(doc["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": tx["redirect_uri"],
            "code_verifier": tx["verifier"]}, auth=(cfg.client_id, cfg.client_secret),
            headers={"Accept": "application/json"})
    if r.status_code != 200:
        # the provider's error code only: its description can echo what was sent
        err = r.json().get("error", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        raise AuthError("login_failed", f"the provider refused the code ({str(err)[:60] or r.status_code})")
    id_token = r.json().get("id_token")
    if not isinstance(id_token, str):
        raise AuthError("login_failed", "the provider sent no ID token")
    return verify_id_token(cfg, doc, id_token, tx["nonce"])


def verify_id_token(cfg: Config, doc: dict, id_token: str, nonce: str) -> dict:
    """The ID token's claims once its signature (the provider's JWKS), issuer, audience, expiry
    and nonce hold. A key id the cached JWKS does not know fetches it once more (key rollover)."""
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet

    def decode(refresh: bool):
        keys = KeySet.import_key_set(_get_json(doc["jwks_uri"], refresh=refresh))
        return jwt.decode(id_token, keys, algorithms=list(ALGORITHMS))

    try:
        try:
            token = decode(False)
        except (JoseError, ValueError):
            token = decode(True)
        claims = token.claims
        jwt.JWTClaimsRegistry(
            leeway=LEEWAY_S,
            iss={"essential": True, "value": doc["issuer"]},
            sub={"essential": True},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(claims)
    except (JoseError, ValueError) as exc:
        raise AuthError("login_failed", f"the ID token does not verify ({type(exc).__name__})") from exc
    aud = claims.get("aud")
    if cfg.client_id not in (aud if isinstance(aud, list) else [aud]):
        raise AuthError("login_failed", "the ID token is for another client")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != cfg.client_id:
        raise AuthError("login_failed", "the ID token is for another client")
    return claims


def logout_url(cfg: Config, after: str) -> str | None:
    """Where the browser ends its session at the provider, when the provider says (end_session_endpoint)."""
    from authlib.common.urls import add_params_to_uri
    try:
        end = discovery(cfg).get("end_session_endpoint")
    except AuthError:
        return None
    if not isinstance(end, str):
        return None
    return add_params_to_uri(end, [("client_id", cfg.client_id), ("post_logout_redirect_uri", after)])


_NEXT = re.compile(r"^#/[A-Za-z0-9_\-/?=&.,%:]*$")


def safe_next(value: str | None) -> str:
    """Where to land after the login: one of the interface's own hashes, else the start page."""
    return value if value and len(value) <= 300 and _NEXT.fullmatch(value) else ""
