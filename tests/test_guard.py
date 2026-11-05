"""What a request has to be before the API acts on it (api/guard.py): the host it names, where a
write comes from, what it carries and how often it comes, and the inventory of the routes that
change something. Runs anywhere: every route refused here is refused before it reads any data.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from kapacitet.api import guard

API = "/api/v1"


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    monkeypatch.delenv("KAPACITET_SUPERVISED", raising=False)   # so an activation that gets through is refused
    with TestClient(app) as c:
        yield c


def _routes(routes, prefix: str = ""):
    """(path, methods) of every API route, through included routers (FastAPI 0.14x keeps those
    as routers of their own rather than copying their routes into the app's list)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, set(r.methods)
        elif hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes, prefix + r.include_context.prefix)


def _code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _small_app(monkeypatch, **env):
    """The guard in front of an app that answers 200, built after the environment is set."""
    from fastapi import FastAPI

    for k in ("CODESPACE_NAME", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "KAPACITET_ALLOWED_HOSTS",
              "KAPACITET_CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = FastAPI()
    app.add_api_route("/api/v1/x", lambda: {"ok": True}, methods=["GET", "POST"])
    app.add_middleware(guard.Guard, limiter=guard.Limiter())
    return app


# ---------------------------------------------------------------- the inventory

def test_every_route_that_writes_has_a_policy_and_every_policy_a_route():
    from kapacitet.api.main import app

    writes = {(m, path) for path, methods in _routes(app.routes) for m in methods & set(guard.UNSAFE)}
    assert writes, "no write routes found"
    missing = sorted(writes - set(guard.ROUTE_POLICY))
    assert not missing, f"routes that write without a policy in api/guard.ROUTE_POLICY: {missing}"
    stale = sorted(set(guard.ROUTE_POLICY) - writes)
    assert not stale, f"policies for routes the app no longer has: {stale}"
    assert all(p.bucket in guard.LIMITS and p.writes for p in guard.ROUTE_POLICY.values())
    reads = {(m, path) for path, methods in _routes(app.routes) for m in methods}
    assert set(guard.LIMITED_READS) <= reads


def test_a_concrete_path_finds_its_routes_policy_and_bucket():
    assert guard.policy_for("POST", f"{API}/datasets/abc123/activate").gate.startswith("KAPACITET_SUPERVISED")
    assert guard.policy_for("DELETE", f"{API}/datasets/abc123") is guard.ROUTE_POLICY[("DELETE", f"{API}/datasets/{{ds_id}}")]
    assert guard.policy_for("GET", f"{API}/queue") is None and guard.policy_for("POST", f"{API}/datasets/a/b/activate") is None
    assert guard.bucket_for("POST", f"{API}/requests") == "compute"
    assert guard.bucket_for("POST", f"{API}/apply/evaluate") == "compute"
    assert guard.bucket_for("PUT", f"{API}/settings") == "write"
    assert guard.bucket_for("POST", f"{API}/no-such-route") == "write"          # unknown writes count as writes
    assert guard.bucket_for("GET", f"{API}/apply/status/KP-1A2B-3C4D") == "status"
    assert guard.bucket_for("GET", f"{API}/inbox") is None
    assert guard.ROUTE_POLICY[("PUT", f"{API}/setup/upload")].body == guard.OCTET


# ---------------------------------------------------------------- host

def test_a_host_the_server_does_not_answer_to_is_refused(client):
    r = client.get(f"{API}/health", headers={"Host": "evil.example"})
    assert r.status_code == 400 and _code(r) == "bad_host"
    for ok in ("localhost:8000", "127.0.0.1:8131", "[::1]:8000", "testserver"):
        assert client.get(f"{API}/health", headers={"Host": ok}).status_code == 200, ok
    assert client.get("/", headers={"Host": "rebound.example:8000"}).status_code == 400   # the page too


def test_a_codespace_answers_to_its_forwarded_names_and_an_operator_adds_others(monkeypatch):
    from fastapi.testclient import TestClient

    plain = TestClient(_small_app(monkeypatch), base_url="http://x-8000.app.github.dev")
    assert plain.get("/api/v1/x").status_code == 400
    # the domain alone is not enough: it has to be a codespace
    alone = TestClient(_small_app(monkeypatch, GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN="app.github.dev"),
                       base_url="http://x-8000.app.github.dev")
    assert alone.get("/api/v1/x").status_code == 400
    cs = TestClient(_small_app(monkeypatch, CODESPACE_NAME="x", GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN="app.github.dev"),
                    base_url="https://x-8000.app.github.dev")
    assert cs.get("/api/v1/x").status_code == 200
    assert cs.get("/api/v1/x", headers={"Host": "app.github.dev.evil.example"}).status_code == 400
    assert cs.get("/api/v1/x", headers={"Host": "evilapp.github.dev"}).status_code == 400
    lan = TestClient(_small_app(monkeypatch, KAPACITET_ALLOWED_HOSTS="192.168.1.20, kapacitet.lan"), base_url="http://kapacitet.lan")
    assert lan.get("/api/v1/x").status_code == 200
    assert lan.get("/api/v1/x", headers={"Host": "192.168.1.20:8000"}).status_code == 200
    anyhost = TestClient(_small_app(monkeypatch, KAPACITET_ALLOWED_HOSTS="*"), base_url="http://whatever.example")
    assert anyhost.get("/api/v1/x").status_code == 200
    # answering to any name lets a page write to the name it came from, and no other site
    assert anyhost.post("/api/v1/x", json={}, headers={"Origin": "https://whatever.example"}).status_code == 200
    assert anyhost.post("/api/v1/x", json={}, headers={"Origin": "https://evil.example"}).status_code == 403


# ---------------------------------------------------------------- where a write comes from

def test_a_write_from_another_site_is_refused_before_it_runs(client):
    url = f"{API}/datasets/reference/activate"
    for origin in ("https://evil.example", "http://localhost.evil.example", "null", "file://", "http://[::2]:8000"):
        r = client.post(url, json={}, headers={"Origin": origin})
        assert r.status_code == 403 and _code(r) == "bad_origin", origin
    # the interface's own origins pass the guard and reach the route, which refuses for its own reason
    for origin in ("http://localhost:5173", "http://127.0.0.1:8000", "http://[::1]:8000", "http://testserver"):
        r = client.post(url, json={}, headers={"Origin": origin})
        assert r.status_code == 409 and _code(r) == "not_supervised", origin


def test_without_an_origin_the_browsers_fetch_metadata_decides(client):
    url = f"{API}/datasets/reference/activate"
    for site in ("cross-site", "same-site"):
        r = client.post(url, json={}, headers={"Sec-Fetch-Site": site})
        assert r.status_code == 403 and _code(r) == "bad_origin", site
    for site in ("same-origin", "none"):
        assert _code(client.post(url, json={}, headers={"Sec-Fetch-Site": site})) == "not_supervised", site
    assert _code(client.post(url, json={})) == "not_supervised"          # curl or a script: no browser headers


def test_an_origin_the_operator_lists_for_cors_may_write(monkeypatch):
    from fastapi.testclient import TestClient

    app = _small_app(monkeypatch, KAPACITET_CORS_ORIGINS="https://dso.example/")
    c = TestClient(app, base_url="http://localhost:8000")
    assert c.post("/api/v1/x", json={}, headers={"Origin": "https://dso.example"}).status_code == 200
    assert c.post("/api/v1/x", json={}, headers={"Origin": "https://other.example"}).status_code == 403


# ---------------------------------------------------------------- what a write carries

def test_a_write_has_to_say_it_sends_json(client):
    url = f"{API}/datasets/reference/activate"
    for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"},
                    {"Content-Type": "multipart/form-data; boundary=x"}, {}):
        r = client.post(url, content=b"{}", headers=headers)
        assert r.status_code == 415 and _code(r) == "bad_content_type", headers
        assert r.json()["detail"]["params"] == {"expected": "application/json"}
    assert _code(client.post(url, content=b"{}", headers={"Content-Type": "application/json; charset=utf-8"})) == "not_supervised"
    # a bodyless DELETE too: the interface sends {} as JSON (lib/api.ts apiWrite)
    assert client.request("DELETE", f"{API}/queue").status_code == 415
    # the setup page's upload is the one write that sends bytes, and only bytes
    up = client.put(f"{API}/setup/upload", params={"offset": 0, "total": 1, "fp": "x|1|1"}, json={})
    assert up.status_code == 415 and up.json()["detail"]["params"] == {"expected": "application/octet-stream"}


def test_reads_are_not_held_to_the_rules_of_writes(client):
    r = client.get(f"{API}/health", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200          # a read cannot change anything, and CORS keeps its answer from the other site


# ---------------------------------------------------------------- how often

def test_the_window_lets_a_client_through_again_once_its_oldest_request_is_a_minute_old():
    now = [1000.0]
    lim = guard.Limiter({"status": 3, "upload": None}, window_s=60.0, clock=lambda: now[0])
    assert [lim.hit("status", "a") for _ in range(3)] == [None, None, None]
    now[0] += 20
    assert lim.hit("status", "a") == 40                  # the first of the three is 20 s old
    assert lim.hit("status", "b") is None                # another address has its own window
    now[0] += 40
    assert lim.hit("status", "a") is None                # the first one left the window
    assert all(lim.hit("upload", "a") is None for _ in range(100))   # chunks of an upload are not counted
    lim.reset()
    assert lim.hit("status", "a") is None


def test_too_many_writes_are_answered_429_with_retry_after(client):
    url = f"{API}/datasets/reference/activate"
    answers = [client.post(url, json={}) for _ in range(guard.LIMITS["write"] + 1)]
    assert all(_code(r) == "not_supervised" for r in answers[:-1])
    last = answers[-1]
    assert last.status_code == 429 and _code(last) == "rate_limited"
    assert 1 <= int(last.headers["retry-after"]) <= 60
    assert last.json()["detail"]["params"]["seconds"] == int(last.headers["retry-after"])
    # a refused write was not counted, and a read is not limited
    assert client.get(f"{API}/health").status_code == 200


# ---------------------------------------------------------------- no read seals

def test_no_read_seals_anything(client, monkeypatch):
    """Sealing is a write (S4): every GET route without a path parameter is called with a seal
    that fails loudly. The two that ask outside services how they are doing are left out."""
    from kapacitet import seals

    def refuse(*_a, **_k):
        raise AssertionError("a GET route sealed something")

    monkeypatch.setattr(seals, "seal", refuse)
    from kapacitet.api.main import app

    skip = {f"{API}/sources/status", f"{API}/now"}
    paths = sorted({path for path, methods in _routes(app.routes) if "GET" in methods
                    and "{" not in path and path not in skip and path.startswith(API)})
    assert f"{API}/operation/schedule.xml" in paths and f"{API}/eipack" in paths
    for path in paths:
        r = client.get(path, params={"cid": 0, "date": "2025-06-15", "export": "true", "issue": "true"})
        assert r.status_code < 500, (path, r.status_code)
