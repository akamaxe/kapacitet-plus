"""Sign-in with OpenID Connect and the roles it grants (kapacitet/auth.py, api/auth.py, api/guard.py).

An in-process identity provider stands in for Entra ID: its discovery document, JWKS and token
endpoint answer through an httpx transport, its ID tokens are RS256 signed with a key made here
with `cryptography`. Nothing leaves the machine and no data is needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kapacitet import actor, auth, seals
from kapacitet.api import guard

API = "/api/v1"
ISSUER = "https://idp.example/tenant/v2.0"
CLIENT_ID = "kapacitet-client-id"
CLIENT_SECRET = "client-secret-" + "s" * 24
SESSION_SECRET = "session-secret-" + "x" * 40


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class FakeProvider:
    """Discovery, JWKS, an authorization step the test drives, and a token endpoint that checks
    the client's secret, the redirect URI and the PKCE verifier before it signs an ID token."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.codes: dict[str, dict] = {}
        self.token_calls = 0
        self.jwks_calls = 0
        self.override: dict = {}         # claims to change in the next ID token

    # -- tokens
    def sign(self, claims: dict, key=None, kid: str | None = None) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": kid or self.kid}
        body = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(claims).encode())
        sig = (key or self.key).sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
        return body + "." + _b64(sig)

    def claims(self, nonce: str, **extra) -> dict:
        now = int(time.time())
        return {"iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-0001", "name": "Ada Planner",
                "iat": now, "exp": now + 600, "nonce": nonce, "roles": ["planner"], **extra}

    def jwks(self) -> dict:
        pub = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256",
                          "n": _int_b64(pub.n), "e": _int_b64(pub.e)}]}

    # -- the browser's visit to the provider
    def authorize(self, url: str, **claims) -> str:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(ISSUER + "/authorize?")
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
        assert "openid" in q["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"],
                            "nonce": q["nonce"], "claims": claims, "state": q["state"]}
        return code

    # -- the server's calls
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/tenant/v2.0/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/keys",
                "end_session_endpoint": ISSUER + "/logout"})
        if path == "/tenant/v2.0/keys":
            self.jwks_calls += 1
            return httpx.Response(200, json=self.jwks())
        if path == "/tenant/v2.0/token" and request.method == "POST":
            self.token_calls += 1
            expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if request.headers.get("authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            grant = self.codes.pop(form.get("code", ""), None)
            if grant is None or form.get("grant_type") != "authorization_code" \
                    or form.get("redirect_uri") != grant["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            challenge = _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
            if challenge != grant["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
            claims = {**self.claims(grant["nonce"], **grant["claims"]), **self.override}
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer",
                                             "id_token": self.sign(claims)})
        return httpx.Response(404)


@pytest.fixture()
def oidc(monkeypatch):
    """Sign-in on, against the fake provider. Every optional variable is set too, so a developer's
    .env cannot add to it."""
    fake = FakeProvider()
    env = {"KAPACITET_AUTH": "oidc", "KAPACITET_OIDC_ISSUER": ISSUER, "KAPACITET_OIDC_CLIENT_ID": CLIENT_ID,
           "KAPACITET_OIDC_CLIENT_SECRET": CLIENT_SECRET, "KAPACITET_SESSION_SECRET": SESSION_SECRET,
           "KAPACITET_OIDC_ROLE_CLAIM": "roles", "KAPACITET_OIDC_ROLE_MAP": "", "KAPACITET_OIDC_SCOPES": "",
           "KAPACITET_OIDC_REDIRECT_URI": "", "KAPACITET_SESSION_HOURS": "8"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(auth, "_HTTP_TRANSPORT", httpx.MockTransport(fake.handle))
    auth.config.cache_clear()
    auth.forget_provider()
    yield fake
    auth.config.cache_clear()
    auth.forget_provider()


def _client(base_url: str = "http://testserver"):
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app
    return TestClient(app, base_url=base_url)


def _sign_in(c, fake: FakeProvider, next_hash: str = "#/request", **claims):
    r = c.get(f"{API}/auth/login", params={"next": next_hash}, follow_redirects=False)
    assert r.status_code == 302, r.text
    code = fake.authorize(r.headers["location"], **claims)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return c.get(f"{API}/auth/callback", params={"code": code, "state": state}, follow_redirects=False)


def _set_cookies(r) -> list[str]:
    return r.headers.get_list("set-cookie")


def _code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# ---------------------------------------------------------------- the login flow

def test_the_login_flow_opens_a_session_that_reads_and_logout_ends_it(oidc):
    c = _client()
    me = c.get(f"{API}/auth/me").json()
    assert me["enabled"] is True and me["user"] is None and not any(me["can"].values())
    r = c.get(f"{API}/seals/key")
    assert r.status_code == 401 and _code(r) == "auth_required"
    assert c.get(f"{API}/health").status_code == 200          # public

    r = _sign_in(c, oidc, roles=["planner", "auditor"])
    assert r.status_code == 302 and r.headers["location"] == "/#/request"
    session = next(h for h in _set_cookies(r) if h.startswith(auth.SESSION_COOKIE + "="))
    assert "HttpOnly" in session and "SameSite=Lax" in session and "Secure" not in session
    assert any(h.startswith(auth.LOGIN_COOKIE + "=;") and "Max-Age=0" in h for h in _set_cookies(r))

    me = c.get(f"{API}/auth/me").json()
    assert me["user"] == {"sub": "subject-0001", "name": "Ada Planner", "roles": ["planner", "auditor"]}
    assert me["can"]["evaluate"] and me["can"]["export"] and not me["can"]["decide"] and not me["can"]["datasets"]
    assert me["needs"]["decide"] == ["approver"]
    assert c.get(f"{API}/seals/key").status_code == 200
    assert _code(c.put(f"{API}/settings", json={"auto_decisions": False})) == "role_required"

    copied = c.cookies.get(auth.SESSION_COOKIE)
    r = c.post(f"{API}/auth/logout", json={})
    assert r.status_code == 200 and r.json()["logout_url"].startswith(ISSUER + "/logout?client_id=")
    assert any(h.startswith(auth.SESSION_COOKIE + "=;") and "Max-Age=0" in h for h in _set_cookies(r))
    assert c.get(f"{API}/seals/key").status_code == 401
    # a copy of the cookie taken before the logout opens nothing either
    assert _client().get(f"{API}/seals/key", headers={"Cookie": f"{auth.SESSION_COOKIE}={copied}"}).status_code == 401


def test_over_https_the_cookies_are_secure(oidc):
    c = _client("https://testserver")
    r = c.get(f"{API}/auth/login", follow_redirects=False)
    assert "Secure" in r.headers["set-cookie"]
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://testserver/api/v1/auth/callback"]
    code = oidc.authorize(r.headers["location"])
    r = c.get(f"{API}/auth/callback", params={"code": code, "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"] == "/"          # no `next`: the start page
    assert all("Secure" in h for h in _set_cookies(r))


def test_a_configured_redirect_uri_and_an_unsafe_next_are_handled(oidc, monkeypatch):
    monkeypatch.setenv("KAPACITET_OIDC_REDIRECT_URI", "https://kapacitet.dso.example/api/v1/auth/callback")
    auth.config.cache_clear()
    c = _client()
    r = c.get(f"{API}/auth/login", params={"next": "https://evil.example/"}, follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://kapacitet.dso.example/api/v1/auth/callback"]
    code = oidc.authorize(r.headers["location"])
    r = c.get(f"{API}/auth/callback", params={"code": code, "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("spoil", ["state", "no_login", "pkce", "provider_error"])
def test_a_login_that_does_not_hold_lands_on_the_start_page_without_a_session(oidc, spoil):
    c = _client()
    r = c.get(f"{API}/auth/login", follow_redirects=False)
    code = oidc.authorize(r.headers["location"])
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    params = {"code": code, "state": state}
    if spoil == "state":
        params["state"] = "another-state"
    elif spoil == "no_login":
        c.cookies.clear()
    elif spoil == "pkce":
        oidc.codes[code]["challenge"] = _b64(hashlib.sha256(b"someone else's verifier").digest())
    else:
        params = {"error": "access_denied", "state": state}
    r = c.get(f"{API}/auth/callback", params=params, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/?signin=login_failed"
    assert not any(h.startswith(auth.SESSION_COOKIE + "=v1.") for h in _set_cookies(r))
    assert c.get(f"{API}/auth/me").json()["user"] is None


@pytest.mark.parametrize("bad", ["nonce", "audience", "issuer", "expired", "other_key", "no_sub"])
def test_an_id_token_that_does_not_verify_opens_no_session(oidc, bad):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = auth.config()
    doc = auth.discovery(cfg)
    claims = oidc.claims("the-nonce")
    nonce = "the-nonce"
    if bad == "nonce":
        nonce = "another-nonce"
    elif bad == "audience":
        claims["aud"] = "another-client"
    elif bad == "issuer":
        claims["iss"] = "https://idp.example/other/v2.0"
    elif bad == "expired":
        claims["exp"] = int(time.time()) - 3600
    elif bad == "no_sub":
        del claims["sub"]
    token = oidc.sign(claims, key=other if bad == "other_key" else None)
    with pytest.raises(auth.AuthError) as exc:
        auth.verify_id_token(cfg, doc, token, nonce)
    assert exc.value.code == "login_failed"
    assert auth.verify_id_token(cfg, doc, oidc.sign(oidc.claims("n")), "n")["sub"] == "subject-0001"


def test_a_rolled_over_signing_key_is_fetched_again(oidc):
    cfg = auth.config()
    doc = auth.discovery(cfg)
    auth.verify_id_token(cfg, doc, oidc.sign(oidc.claims("n")), "n")
    assert oidc.jwks_calls == 1
    oidc.key, oidc.kid = rsa.generate_private_key(public_exponent=65537, key_size=2048), "key-2"
    assert auth.verify_id_token(cfg, doc, oidc.sign(oidc.claims("n")), "n")["sub"] == "subject-0001"
    assert oidc.jwks_calls == 2


def test_a_session_cookie_is_signed_bound_to_its_purpose_and_expires(oidc):
    cfg = auth.config()
    s = auth.new_session(oidc.claims("n"), cfg)
    value = auth.sign(s, cfg.session_key, "session")
    assert auth.unsign(value, cfg.session_key, "session")["sub"] == "subject-0001"
    assert auth.unsign(value, cfg.session_key, "login") is None               # another purpose
    assert auth.unsign(value, b"k" * 32, "session") is None                   # another key
    ver, body, mac = value.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged["roles"] = ["admin"]
    assert auth.unsign(f"{ver}.{_b64(json.dumps(forged).encode())}.{mac}", cfg.session_key, "session") is None
    assert auth.unsign(value, cfg.session_key, "session", now=s["exp"] + 1) is None
    assert auth.unsign("garbage", cfg.session_key, "session") is None


# ---------------------------------------------------------------- roles

def test_the_role_map_takes_pairs_or_json_and_refuses_unknown_roles():
    assert auth.parse_role_map("Kapacitet.Planner=planner, Kapacitet.Approver=approver") == {
        "Kapacitet.Planner": "planner", "Kapacitet.Approver": "approver"}
    assert auth.parse_role_map('{"DSO.Admin": "admin"}') == {"DSO.Admin": "admin"}
    assert auth.parse_role_map("") == {}
    for bad in ("X=superuser", "no-equals", "[1]", "{bad json"):
        with pytest.raises(auth.AuthConfigError):
            auth.parse_role_map(bad)


def test_roles_come_from_the_configured_claim_through_the_map(oidc, monkeypatch):
    cfg = auth.config()
    assert auth.roles_from_claims({"roles": ["auditor", "planner", "Global Admin"]}, cfg) == ["planner", "auditor"]
    assert auth.roles_from_claims({"roles": "approver admin"}, cfg) == ["approver", "admin"]
    assert auth.roles_from_claims({}, cfg) == []
    monkeypatch.setenv("KAPACITET_OIDC_ROLE_CLAIM", "groups")
    monkeypatch.setenv("KAPACITET_OIDC_ROLE_MAP", "Kapacitet.Approver=approver,Kapacitet.Reader=auditor")
    auth.config.cache_clear()
    cfg = auth.config()
    assert auth.roles_from_claims({"groups": ["Kapacitet.Reader", "approver"], "roles": ["admin"]}, cfg) == ["auditor"]


def test_a_person_without_a_role_is_signed_in_but_reads_nothing(oidc):
    c = _client()
    _sign_in(c, oidc, roles=["Some.Other.App.Role"])
    me = c.get(f"{API}/auth/me").json()
    assert me["user"]["roles"] == [] and not any(me["can"].values())
    r = c.get(f"{API}/seals/key")
    assert r.status_code == 403 and _code(r) == "role_required"


def _fill(template: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "abc123def456", template)


def _scope(method: str, path: str, cookie: str | None, body: str = guard.JSON) -> dict:
    headers = [(b"host", b"testserver")]
    if method in guard.UNSAFE:
        headers.append((b"content-type", body.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return {"type": "http", "method": method, "path": path, "headers": headers, "client": ("127.0.0.1", 1)}


def _cookie_for(roles: list[str]) -> str:
    cfg = auth.config()
    s = auth.new_session({"sub": "subject-" + "-".join(roles), "name": "Test Person", "roles": roles}, cfg)
    return f"{auth.SESSION_COOKIE}={auth.sign(s, cfg.session_key, 'session')}"


CASES = [(m, t, p) for (m, t), p in guard.ROUTE_POLICY.items()]


@pytest.mark.parametrize("method,template,policy", CASES, ids=[f"{m} {t}" for m, t, _ in CASES])
def test_every_route_in_the_policy_takes_exactly_its_roles(oidc, method, template, policy):
    g = guard.Guard(lambda *a: None, limiter=guard.Limiter(limits={k: None for k in guard.LIMITS}))
    path = _fill(template)
    for who in [None, *[[r] for r in auth.ROLES]]:
        scope = _scope(method, path, _cookie_for(who) if who else None, policy.body)
        refused = g.check(scope)
        if not policy.roles:
            assert refused is None, (who, "a public route")
            assert guard.ACTOR_KEY not in scope        # the portal is anonymous, even for staff
        elif who is None:
            assert refused is not None and refused.status_code == 401
        elif who[0] in policy.roles:
            assert refused is None, (who, policy.roles)
            assert scope[guard.ACTOR_KEY] == {"sub": "subject-" + who[0], "name": "Test Person", "role": who[0]}
        else:
            assert refused is not None and refused.status_code == 403
            assert json.loads(refused.body)["detail"]["params"]["roles"] == ", ".join(policy.roles)


def test_the_policy_says_what_the_plan_asks_of_each_kind_of_route():
    P = guard.ROUTE_POLICY
    assert P[("POST", f"{API}/queue")].roles == ("approver",)                     # decisions: approver
    for k in [("PUT", f"{API}/settings"), ("PUT", f"{API}/settings/rule-signature"),
              ("POST", f"{API}/operation/market-alternatives")]:
        assert set(P[k].roles) == {"admin", "approver"}
    for k in [("POST", f"{API}/requests"), ("POST", f"{API}/evaluate")]:
        assert set(P[k].roles) == {"planner", "approver", "admin"}                # evaluations: planner and above
    assert all(not p.roles for (m, t), p in P.items() if t.startswith(f"{API}/apply/"))
    assert all("auditor" not in p.roles or t == f"{API}/eipack/export" for (m, t), p in P.items())


@pytest.mark.parametrize("path,public", [
    (f"{API}/queue", False), (f"{API}/state/doe/bin", False), (f"{API}/seals", False), (f"{API}/health", True),
    (f"{API}/auth/me", True), (f"{API}/apply/houses", True), (f"{API}/apply/status/ABCD-1234", True),
    ("/", True), ("/assets/index.js", True), (f"{API}/healthz", False)])
def test_reads_need_any_role_but_the_public_ones(oidc, path, public):
    g = guard.Guard(lambda *a: None, limiter=guard.Limiter(limits={k: None for k in guard.LIMITS}))
    assert (g.check(_scope("GET", path, None)) is None) is public
    for r in auth.ROLES:
        assert g.check(_scope("GET", path, _cookie_for([r]))) is None


def test_with_sign_in_off_the_guard_asks_nobody_and_there_is_no_actor():
    auth.config.cache_clear()
    g = guard.Guard(lambda *a: None, limiter=guard.Limiter(limits={k: None for k in guard.LIMITS}))
    for (method, template), policy in guard.ROUTE_POLICY.items():
        scope = _scope(method, _fill(template), None, policy.body)
        assert g.check(scope) is None and guard.ACTOR_KEY not in scope
    me = _client().get(f"{API}/auth/me").json()
    assert me["enabled"] is False and me["user"] is None and all(me["can"].values())
    assert _client().get(f"{API}/auth/login", follow_redirects=False).status_code == 404


def test_sign_in_asked_for_but_not_configured_fails_closed_and_names_no_value(monkeypatch, caplog):
    import logging

    auth._REPORTED.clear()                              # each reason is logged once per process
    caplog.set_level(logging.ERROR, logger="kapacitet.auth")
    monkeypatch.setenv("KAPACITET_AUTH", "oidc")
    monkeypatch.setenv("KAPACITET_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("KAPACITET_OIDC_CLIENT_SECRET", CLIENT_SECRET)
    for k in ("KAPACITET_OIDC_CLIENT_ID", "KAPACITET_SESSION_SECRET"):
        monkeypatch.setenv(k, "")
    auth.config.cache_clear()
    try:
        r = _client().get(f"{API}/seals/key")
        assert r.status_code == 503 and _code(r) == "auth_misconfigured"
        # the reply says that sign-in cannot run, to anyone; what is missing is in the server's log
        assert r.json()["detail"]["params"] == {} and "KAPACITET_" not in r.text
        said = " ".join(rec.getMessage() for rec in caplog.records)
        assert "KAPACITET_OIDC_CLIENT_ID" in said and "KAPACITET_SESSION_SECRET" in said
        assert CLIENT_SECRET not in r.text and ISSUER not in r.text
        monkeypatch.setenv("KAPACITET_OIDC_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("KAPACITET_SESSION_SECRET", "too-short")
        auth.config.cache_clear()
        r = _client().get(f"{API}/health")
        assert r.status_code == 503 and "too-short" not in r.text
        assert _client().get("/").status_code != 503      # the interface still loads, to say so
        me = _client().get(f"{API}/auth/me")               # and /auth/me tells it why, in its words
        assert me.status_code == 200 and "too-short" not in me.text
        body = me.json()
        # this route answers anyone, so it says that sign-in is unusable and never what is missing
        assert body["enabled"] and body["user"] is None and body["misconfigured"] is True
        assert "KAPACITET_SESSION_SECRET" not in me.text
        assert "KAPACITET_SESSION_SECRET must be at least" in " ".join(rec.getMessage() for rec in caplog.records)
        assert not any(body["can"].values())
        monkeypatch.setenv("KAPACITET_AUTH", "saml")
        auth.config.cache_clear()
        with pytest.raises(auth.AuthConfigError):
            auth.config()
    finally:
        auth.config.cache_clear()


# ---------------------------------------------------------------- who decides, in each seal

def test_a_seal_made_for_a_signed_in_person_carries_them_under_its_signature():
    who = {"sub": "subject-9", "name": "Bo Approver", "role": "approver"}
    with actor.acting_as(who):
        entry = seals.seal("settings", "20261107T000000000000Z", {"x": 1})
    plain = seals.seal("settings", "20261107T000000000001Z", {"x": 2})
    assert entry["actor"] == who and "actor" not in plain
    assert seals.verify_chain()["ok"]
    # the signature covers it: another name breaks the line
    lines = seals._lines()
    forged = {**lines[-2], "actor": {**who, "name": "Someone Else"}}
    pub = seals.signing_key().public_key()
    assert not seals._sig_valid(pub, forged["key_id"], forged) and seals._sig_valid(pub, lines[-2]["key_id"], lines[-2])


def test_a_change_made_through_the_api_is_sealed_with_who_made_it(oidc, tmp_path, monkeypatch):
    from kapacitet import dso

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "settings.json")   # never the data folder's
    c = _client()
    _sign_in(c, oidc, roles=["approver", "admin"], name="Bo Approver", sub="subject-0042")
    before = len(seals._lines())
    r = c.put(f"{API}/settings", json={"deferral_years": dso.load().deferral_years % 40 + 1})
    assert r.status_code == 200, r.text
    assert len(seals._lines()) == before + 1          # a real change, so a new line
    last = seals._lines()[-1]
    assert last["kind"] == "settings"
    # settings take admin or approver, in that order: the admin role is the one it is taken under
    assert last["actor"] == {"sub": "subject-0042", "name": "Bo Approver", "role": "admin"}


def test_the_rule_signature_names_the_session_and_not_the_free_text(oidc, tmp_path, monkeypatch):
    from kapacitet import dso

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "settings.json")
    c = _client()
    _sign_in(c, oidc, roles=["approver"], name="Bo Approver", sub="subject-0042")
    r = c.put(f"{API}/settings/rule-signature",
              json={"signed_by": "Mallory", "role": "head of grid planning", "request_kinds": ["pv"],
                    "max_pv_kwp": 12, "valid_until": "2099-12-31"})
    assert r.status_code == 200, r.text
    sig = dso.load().rule_signature
    who = {"sub": "subject-0042", "name": "Bo Approver", "role": "approver"}
    assert sig.signed_by == "Bo Approver" and sig.actor == who and sig.role == "head of grid planning"
    # the digest the automatic decisions keep covers who signed
    assert dso.signature_digest(sig) != dso.signature_digest({**dso.signature_doc(sig), "actor": None})
    sealed = seals.entry_for("rule_signature", dso.signature_digest(sig)[:16])
    assert sealed["actor"] == who
    # sign-in off: the free text stays, and the digest is the one it had before `actor` existed
    plain = dso.signature_of(dso.RuleSignatureIn(signed_by="Mallory", role="x", request_kinds=["pv"],
                                                 max_pv_kwp=1, valid_until="2099-12-31"))
    assert plain.signed_by == "Mallory" and plain.actor is None and "actor" not in dso.signature_doc(plain)


def test_a_decision_records_the_approver_and_seals_under_them(oidc, monkeypatch):
    from kapacitet.api import main, records
    from kapacitet.engine import queue as qu

    seen: dict = {}
    done = threading.Event()

    def decide(model, hp, ev, rec, decision, note="", emit=None, decided_by="", actor=None, **kw):
        seen.update(actor=actor, decided_by=decided_by, context=seals_actor())
        done.set()
        return {"assessment": rec["id"], "decision": decision, "actor": actor}

    def seals_actor():
        from kapacitet import actor as act
        return act.current()

    monkeypatch.setattr(records, "load", lambda job_id, **kw: {"id": job_id, "request": {"cid": 1}, "result": {}})
    monkeypatch.setattr(qu, "_check", lambda q, rec, decision: "static")
    monkeypatch.setattr(qu, "decide", decide)
    monkeypatch.setattr(main, "_engine", lambda: {"model": None, "hp": None, "ev": None})
    monkeypatch.setattr(main.inbox, "link_decision", lambda *a, **k: None)
    c = _client()
    _sign_in(c, oidc, roles=["planner"])
    r = c.post(f"{API}/queue", json={"assessment": "0123456789ab", "decision": "declined"})
    assert r.status_code == 403 and _code(r) == "role_required"

    c = _client()
    _sign_in(c, oidc, roles=["approver"], name="Bo Approver", sub="subject-0042")
    r = c.post(f"{API}/queue", json={"assessment": "0123456789ab", "decision": "declined", "decided_by": "Mallory"})
    assert r.status_code == 200, r.text
    assert done.wait(10)
    who = {"sub": "subject-0042", "name": "Bo Approver", "role": "approver"}
    assert seen["actor"] == who and seen["context"] == who


def test_queue_decide_keeps_the_actor_and_its_name_over_the_free_text(tmp_path, monkeypatch):
    """The engine's side: a refusal needs no network, so the item and its seal can be read here."""
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    qu.reset()
    who = {"sub": "subject-0042", "name": "Bo Approver", "role": "approver"}
    rec = {"id": "0123456789ab", "request": {"cid": 3, "baseline": "static"}, "result": {}}
    with actor.acting_as(who):
        item = qu.decide(None, None, None, rec, "declined", "no room", decided_by="Mallory", actor=who)
    assert item["actor"] == who and item["decided_by"] == "Bo Approver"
    entry = seals.entry_for("decision", "0123456789ab")
    assert entry["actor"] == who and entry["digest"] == seals.digest(qu.load()["items"][0])
    # sign-in off: the free text stays, and nothing names an actor
    rec2 = {**rec, "id": "0123456789ac", "request": {"cid": 4, "baseline": "static"}}
    item = qu.decide(None, None, None, rec2, "declined", decided_by="Mallory")
    assert item["decided_by"] == "Mallory" and "actor" not in item
    assert "actor" not in seals.entry_for("decision", "0123456789ac")
    qu.reset()


# ---------------------------------------------------------------- secrets stay out of the logs

def test_no_secret_reaches_a_log_line(oidc, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    c = _client()
    r = c.get(f"{API}/auth/login", follow_redirects=False)
    code = oidc.authorize(r.headers["location"])
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = c.get(f"{API}/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    cookie = c.cookies.get(auth.SESSION_COOKIE)
    c.get(f"{API}/auth/callback", params={"code": "used-up", "state": state}, follow_redirects=False)  # a failure
    monkeypatch.setenv("KAPACITET_SESSION_SECRET", "tiny-secret-7f3")
    auth.config.cache_clear()
    _client().get(f"{API}/health")
    # the test's own client logs the browser's side of the redirects; the server's lines are the rest
    server = [rec for rec in caplog.records if "http://testserver" not in rec.getMessage()]
    text = "\n".join(f"{rec.name} {rec.getMessage()} {rec.args!r}" for rec in server)
    for secret in (CLIENT_SECRET, SESSION_SECRET, "tiny-secret-7f3", code, cookie,
                   base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()):
        assert secret and secret not in text
    assert "signed in" in text                       # the log does say what happened
    assert repr(auth.Config(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                            session_key=b"k" * 32)).count(CLIENT_SECRET) == 0


def test_the_access_log_never_carries_the_callbacks_code():
    from kapacitet.api.auth import RedactCallback

    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/api/v1/auth/callback?code=abc123secret&state=xyz", "1.1", 302),
                            None)
    assert RedactCallback().filter(rec)
    assert "abc123secret" not in rec.getMessage() and "/api/v1/auth/callback?[redacted]" in rec.getMessage()
    assert any(isinstance(f, RedactCallback) for f in logging.getLogger("uvicorn.access").filters)


def test_a_provider_that_does_not_answer_is_said_and_never_a_server_error(oidc, monkeypatch):
    """With the identity provider down, login answered 500, the callback 500 and logout 500 after
    revoking the session without clearing its cookie. Each now says provider_unavailable, and a
    logout still ends the session here."""
    c = _client()
    assert _sign_in(c, oidc, roles=["planner"]).status_code == 302
    assert c.get(f"{API}/auth/me").json()["user"] is not None

    def down(request):
        raise httpx.ConnectError("getaddrinfo failed for login.example.org", request=request)

    def start(next_hash="#/request"):
        return c.get(f"{API}/auth/login", params={"next": next_hash}, follow_redirects=False)

    # a login that started while the provider still answered
    began = start()
    code = oidc.authorize(began.headers["location"], roles=["planner"])
    state = parse_qs(urlsplit(began.headers["location"]).query)["state"][0]
    monkeypatch.setattr(auth, "_HTTP_TRANSPORT", httpx.MockTransport(down))
    auth.forget_provider()                                     # nothing remembered of its documents
    r = start()
    assert r.status_code == 502 and _code(r) == "provider_unavailable"
    assert "login.example.org" not in r.text and "ConnectError" in r.text      # the kind, not the address
    back = c.get(f"{API}/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"] == "/?signin=provider_unavailable"
    out = c.post(f"{API}/auth/logout", json={})
    assert out.status_code == 200 and out.json() == {"ok": True, "logout_url": None}
    assert any(h.startswith(auth.SESSION_COOKIE + "=;") or f"{auth.SESSION_COOKIE}=" in h and "Max-Age=0" in h
               for h in _set_cookies(out))
    # a reply that is no JSON is the same kind of failure
    monkeypatch.setattr(auth, "_HTTP_TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, text="<html>")))
    auth.forget_provider()
    r = start()
    assert r.status_code == 502 and _code(r) == "provider_unavailable"
