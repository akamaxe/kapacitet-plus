"""Telemetry (kapacitet/telemetry.py): off, nothing is imported or installed; on, an evaluation,
a decision and an API call produce their spans and metrics, and no address, reference code or
token reaches the exporter. No data needed: the engine is a stub."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"
STREET = "Testvägen 1"
REFERENCE = "KP-7Q4M-X9ZT"
TOKEN = "s3cr3t-token-do-not-export"
SRC = Path(__file__).resolve().parents[1] / "src"


def _clean_env(tmp_path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_") and k != "KAPACITET_TELEMETRY"}
    env["KAPACITET_DATA_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")])
    return env


# FastAPI itself depends on the OpenTelemetry API (its own bridge, which main.py turns off), so the
# API package is always there; what telemetry adds is the SDK, the exporter and the instrumentation.
ADDED = ("opentelemetry.sdk", "opentelemetry.exporter", "opentelemetry.instrumentation")


def test_off_nothing_is_imported_or_installed(tmp_path):
    """Without the variables the server is what it was: no SDK, exporter or instrumentation module,
    no middleware, the default HTTP exception handler, no global provider, FastAPI's own bridge
    off, and the helpers do nothing."""
    code = """
import json, sys
from kapacitet.api import main
from kapacitet import telemetry
from fastapi.exception_handlers import http_exception_handler
from opentelemetry import metrics, trace
from starlette.exceptions import HTTPException
with telemetry.span("kapacitet.evaluation", cid=1) as sp:
    sp.stage("static", 1.0)
    sp.set(outcome="done")
telemetry.evaluated("done", 0.1)
telemetry.decided("firm", "done", 0.1)
telemetry.refused("busy", 429)
print(json.dumps({
    "enabled": telemetry.enabled(), "context": telemetry.context() is None,
    "modules": sorted(m for m in sys.modules if m.startswith(%r)),
    "providers": [type(trace.get_tracer_provider()).__name__, type(metrics.get_meter_provider()).__name__],
    "native": main.app._native_telemetry.enabled(),
    "middleware": [repr(m) for m in main.app.user_middleware if "elemetry" in repr(m)],
    "handler": main.app.exception_handlers.get(HTTPException) is http_exception_handler,
    "marked": getattr(main.app.state, "kapacitet_telemetry", False),
}))
""" % (ADDED,)
    out = subprocess.run([sys.executable, "-c", code], env=_clean_env(tmp_path), capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == {"enabled": False, "context": True, "modules": [], "middleware": [], "handler": True,
                   "marked": False, "providers": ["ProxyTracerProvider", "_ProxyMeterProvider"], "native": False}


@pytest.mark.parametrize("env, want", [
    ({}, None),
    ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, "otlp"),
    ({"KAPACITET_TELEMETRY": "console"}, "console"),
    ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "KAPACITET_TELEMETRY": "off"}, None),
    ({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}, None),
])
def test_the_environment_decides_whether_it_is_on(monkeypatch, env, want):
    from kapacitet import telemetry

    for key in ("OTEL_EXPORTER_OTLP_ENDPOINT", "KAPACITET_TELEMETRY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert telemetry.mode() == want


def test_attributes_outside_the_whitelist_are_dropped():
    from kapacitet import telemetry

    got = telemetry._attrs(cid=3, address=STREET, reference=REFERENCE, token=TOKEN, outcome="x" * 500)
    assert got == {"kapacitet.cid": 3, "kapacitet.outcome": "x" * 64}


# ---------------------------------------------------------------- on, with in-memory exporters

@pytest.fixture()
def otel(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from kapacitet import telemetry

    for key in ("OTEL_EXPORTER_OTLP_ENDPOINT", "KAPACITET_TELEMETRY"):
        monkeypatch.delenv(key, raising=False)
    telemetry.shutdown()
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    assert telemetry.setup(span_exporter=exporter, metric_reader=reader)
    try:
        yield exporter, reader
    finally:
        telemetry.shutdown()
    assert not telemetry.enabled()


def _metrics(reader) -> dict[str, list[dict]]:
    """name -> the attributes of each data point."""
    out: dict[str, list[dict]] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out.setdefault(m.name, []).extend(dict(p.attributes) for p in m.data.data_points)
    return out


def _exported(exporter) -> str:
    return "\n".join(s.to_json() for s in exporter.get_finished_spans())


def _assert_nothing_personal(text: str, *also: str) -> None:
    for secret in (STREET, "Testvägen", REFERENCE, TOKEN, *also):
        assert secret not in text, f"{secret!r} reached the exporter"


def test_an_evaluation_produces_its_spans_and_no_personal_data(otel, monkeypatch, tmp_path):
    from kapacitet.api import main, records
    from kapacitet.engine import request as rq

    exporter, reader = otel
    stages = ("static", "envelope", "verify", "risk", "economics")

    def evaluate(model, req, hp, ev, emit=None, ctx=None):
        for stage in stages:
            time.sleep(0.002)
            ctx.check()
            emit(stage, {"ms": 2.0})
        result = {"request": dict(req.__dict__), "customer": {"cid": req.cid, "address": STREET},
                  "summary": {"pv_static_approved": True}, "seconds": 0.01}
        emit("done", {"summary": result["summary"], "seconds": 0.01})
        return result

    monkeypatch.setattr(main, "_check_cid", lambda cid: None)
    monkeypatch.setattr(main, "_engine", lambda: {"model": None, "hp": None, "ev": None})
    monkeypatch.setattr(rq, "evaluate", evaluate)
    monkeypatch.setattr(records, "folder", lambda: tmp_path)
    monkeypatch.setattr(records, "address_of", lambda cid: STREET)

    with TestClient(main.app) as client:
        job_id = client.post(f"{API}/requests", json={"cid": 3, "pv_kwp": 6.0}).json()["id"]
        for _ in range(200):
            r = client.get(f"{API}/requests/{job_id}")
            if r.status_code == 200:
                break
            time.sleep(0.02)
        assert r.status_code == 200 and r.json()["customer"]["address"] == STREET

    spans = exporter.get_finished_spans()
    top = [s for s in spans if s.name == "kapacitet.evaluation"]
    assert len(top) == 1
    ev = top[0]
    assert dict(ev.attributes) == {"kapacitet.cid": 3, "kapacitet.baseline": "static", "kapacitet.source": "dso",
                                   "kapacitet.outcome": "done"}
    children = sorted((s for s in spans if s.parent is not None and s.parent.span_id == ev.context.span_id),
                      key=lambda s: s.start_time)
    assert [s.name for s in children] == [f"kapacitet.evaluation.{st}" for st in stages]
    assert [s.attributes["kapacitet.stage"] for s in children] == list(stages)
    assert all(ev.start_time <= c.start_time <= c.end_time <= ev.end_time for c in children)
    _assert_nothing_personal(_exported(exporter), job_id)

    m = _metrics(reader)
    assert {"kapacitet.outcome": "done", "kapacitet.baseline": "static", "kapacitet.source": "dso"} \
        in m["kapacitet.evaluations"]
    assert m["kapacitet.evaluation.duration"]


def test_a_decision_is_traced_and_counted_by_outcome(otel, tmp_path):
    from kapacitet.engine import queue as qu

    exporter, reader = otel
    record = {"id": "abcdefabcdef", "request": {"cid": 4, "baseline": "static", "pv_kwp": 3.0},
              "result": {"summary": {}, "customer": {"cid": 4, "address": STREET}}}
    with pytest.raises(qu.QueueError):
        qu.decide(None, None, None, record, "maybe", where=tmp_path / "queue")
    item = qu.decide(None, None, None, record, "declined", note="no", decided_by="Anna Planner",
                     where=tmp_path / "queue")
    assert item["decision"] == "declined"

    spans = [s for s in exporter.get_finished_spans() if s.name == "kapacitet.queue.decide"]
    assert [dict(s.attributes) for s in spans] == [
        {"kapacitet.cid": 4, "kapacitet.decision": "maybe", "kapacitet.by": "dso", "kapacitet.outcome": "refused",
         "kapacitet.code": "unknown_decision", "error.type": "QueueError"},
        {"kapacitet.cid": 4, "kapacitet.decision": "declined", "kapacitet.by": "dso", "kapacitet.outcome": "done"},
    ]
    assert spans[0].status.description is None
    _assert_nothing_personal(_exported(exporter), "Anna Planner", "abcdefabcdef")
    counted = _metrics(reader)["kapacitet.decisions"]
    assert {"kapacitet.decision": "declined", "kapacitet.outcome": "done", "kapacitet.by": "dso"} in counted
    assert {"kapacitet.decision": "maybe", "kapacitet.outcome": "refused", "kapacitet.by": "dso",
            "kapacitet.code": "unknown_decision"} in counted


def test_the_http_layer_keeps_the_route_never_the_path_query_or_headers(otel):
    """The FastAPI instrumentation, filtered: a reference code in the path, a sign-in code in the
    query and a token in the headers stay out of every span and metric; a refusal is counted by code."""
    from fastapi import FastAPI, HTTPException

    from kapacitet import telemetry

    exporter, reader = otel
    app = FastAPI()

    @app.get("/api/v1/apply/status/{reference}")
    def status(reference: str) -> dict:
        raise HTTPException(404, {"code": "unknown_reference", "message": f"no application {reference}",
                                  "params": {"reference": reference}})

    @app.get("/api/v1/auth/callback")
    def callback(code: str, state: str) -> dict:
        raise RuntimeError(f"cannot exchange {code}")

    assert telemetry.setup(app)
    assert telemetry.setup(app)               # instrumented once
    headers = {"Authorization": f"Bearer {TOKEN}", "Cookie": f"kapacitet_session={TOKEN}",
               "X-Forwarded-For": "203.0.113.9", "User-Agent": f"agent-{TOKEN}"}
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(f"/api/v1/apply/status/{REFERENCE}", headers=headers)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_reference"
        assert client.get(f"/api/v1/auth/callback?code={TOKEN}&state={REFERENCE}",
                          headers=headers).status_code == 500

    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert "GET /api/v1/apply/status/{reference}" in names and "GET /api/v1/auth/callback" in names
    allowed = telemetry.KEYS | telemetry.HTTP_KEYS
    assert all(set(s.attributes) <= allowed for s in spans)
    assert all(e.name and set(e.attributes) <= {"exception.type"} for s in spans for e in s.events)
    _assert_nothing_personal(_exported(exporter), "203.0.113.9", "agent-")

    m = _metrics(reader)
    assert {"kapacitet.code": "unknown_reference", "kapacitet.status": 404} in m["kapacitet.refusals"]
    http = [p for name, points in m.items() if name.startswith("http.server") for p in points]
    assert http and all(set(p) <= telemetry.HTTP_KEYS for p in http)
    _assert_nothing_personal(json.dumps(m, default=str), "203.0.113.9")


def test_a_station_build_traces_each_step(otel, monkeypatch, tmp_path):
    from kapacitet import procs
    from kapacitet.study import station

    exporter, _ = otel
    seen: list[str] = []

    class FakeStudy:
        class config:
            class dso:
                method_version = station.METHOD_VERSION

        def record(self, sid):
            return {"id": sid}

        def building(self, sid):
            return False

        def digests(self, sid):
            return {}

        def save(self, rec):
            pass

        def station_dir(self, sid):
            return tmp_path / sid

    monkeypatch.setattr(procs, "lock_path", lambda d: tmp_path / "build.lock")
    steps = {name: (lambda n=name: seen.append(n)) for name in station.STEPS}
    out = station.build(FakeStudy(), "S-17", quick=True, steps=steps)
    assert out["status"] == "ready" and seen == list(station.STEPS)

    spans = exporter.get_finished_spans()
    build = [s for s in spans if s.name == "kapacitet.station.build"]
    assert len(build) == 1
    assert dict(build[0].attributes) == {"kapacitet.station": "S-17", "kapacitet.quick": True,
                                         "kapacitet.outcome": "ready"}
    steps_traced = [s for s in spans if s.name == "kapacitet.station.build.step"]
    assert [s.attributes["kapacitet.step"] for s in sorted(steps_traced, key=lambda s: s.start_time)] \
        == list(station.STEPS)
    assert all(s.parent.span_id == build[0].context.span_id for s in steps_traced)


def test_a_schedule_issue_is_traced_and_a_replay_says_so(otel, monkeypatch):
    """The issue span carries the policy, the step and whether the key replayed a first issue, and
    nothing else but the house's customer id and the date."""
    import numpy as np
    from types import SimpleNamespace

    from kapacitet.api import main, operation
    from kapacitet.engine import delivery

    exporter, _ = otel
    year = 2025
    limits = np.full(len(delivery.year_index(year)), 2.0)
    combo = SimpleNamespace(policy="maxmin")
    day = delivery.day_of(year, __import__("datetime").date(2025, 6, 2))
    monkeypatch.setattr(operation, "_day_ahead", lambda cid, date, policy: (combo, year, day, limits, None, 1.0, 1.0))
    monkeypatch.setattr(operation, "_market_terms", lambda body, step, rows: {"flags": [], "market_alternatives": []})
    fake = {"entry": {"id": "x", "digest": "d" * 64, "seq": 1, "at": "2026-01-01T00:00:00+00:00"},
            "record": {}}
    sealed: list[dict] = []

    def seal(kind, item, obj):
        sealed.append({"entry": {**fake["entry"], "id": item}, "record": obj})
        return sealed[-1]["entry"]

    monkeypatch.setattr(operation.seals, "fingerprint", lambda: {"digest": "abcdef0123456789"})
    monkeypatch.setattr(operation.seals, "seal", seal)
    monkeypatch.setattr(operation.seals, "sealed_of_kind", lambda kind: list(sealed))
    key = {"Idempotency-Key": "inverter-retry-0001"}
    payload = {"cid": 5, "date": "2025-06-02", "resolution": 60}

    with TestClient(main.app) as client:
        assert client.post(f"{API}/operation/schedule/issue", json=payload, headers=key).status_code == 200
        assert client.post(f"{API}/operation/schedule/issue", json=payload, headers=key).status_code == 200

    spans = [s for s in exporter.get_finished_spans() if s.name == "kapacitet.schedule.issue"]
    assert [dict(s.attributes) for s in spans] == [
        {"kapacitet.cid": 5, "kapacitet.date": "2025-06-02", "kapacitet.policy": "maxmin",
         "kapacitet.resolution_min": 60, "kapacitet.replayed": replayed} for replayed in (False, True)]


def test_a_rule_evaluation_is_traced_with_its_stages(otel, monkeypatch):
    from kapacitet.api import main
    from kapacitet.engine import request as rq

    exporter, _ = otel

    def evaluate(model, req, hp, ev, emit=None, ctx=None):
        for stage in ("static", "envelope"):
            emit(stage, {"ms": 1.0})
        emit("done", {})
        return {"ok": True}

    monkeypatch.setattr(main, "_engine", lambda: {"model": None, "hp": None, "ev": None})
    monkeypatch.setattr(rq, "evaluate", evaluate)
    assert main.evaluate_record({"cid": 3, "pv_kwp": 6.0}) == {"ok": True}

    spans = exporter.get_finished_spans()
    top = [s for s in spans if s.name == "kapacitet.evaluation"]
    assert len(top) == 1 and top[0].attributes["kapacitet.source"] == "rule"
    assert top[0].attributes["kapacitet.outcome"] == "done"
    kids = [s.name for s in spans if s.parent is not None and s.parent.span_id == top[0].context.span_id]
    assert sorted(kids) == ["kapacitet.evaluation.envelope", "kapacitet.evaluation.static"]
