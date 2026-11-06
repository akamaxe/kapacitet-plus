"""Applications and the rule that decides them (engine/rules.py, api/inbox.py).

The rule and the indicators are pure and run anywhere; the worker is driven here with a fake engine
and a fake queue decision, so the order of its steps and its lock are tested without data. The last
test runs a real automatic approval and needs the pipeline's output.
"""

from __future__ import annotations

import random
import threading
import time

import pytest

from conftest import needs_results

def _th(mean=3.0, p90=3.0, ev=1.0):
    from kapacitet.engine.rules import Thresholds

    return Thresholds(curtailment_mean_pct=mean, curtailment_p90_pct=p90, ev_unserved_pct=ev)


def _result(pv_ok=False, ev=None, hp=None, mean=1.0, p90=2.0, ev_unmet=0.0, hp_unmet=0.0, fuse=True, left=0):
    static = {"pv": {"approved": pv_ok}}
    if ev is not None:
        static["ev"] = {"approved": ev}
    if hp is not None:
        static["hp"] = {"approved": hp}
    return {"static": static, "risk": {"mean_pct": mean, "p90_pct": p90},
            "summary": {"ev_unmet_pct": ev_unmet, "hp_unmet_pct": hp_unmet},
            "fuse": {"ok": fuse}, "verification": {"violations_after": left}}


# ---------------------------------------------------------------- the rule (no data)

def test_firm_only_when_every_static_check_passes():
    from kapacitet.engine.rules import verdict

    assert verdict(_result(pv_ok=True), _th())["outcome"] == "firm"
    assert verdict(_result(pv_ok=True, ev=True), _th())["outcome"] == "firm"
    # one part rejected: the envelope's case, never firm
    assert verdict(_result(pv_ok=True, ev=False), _th())["outcome"] == "nonfirm"
    assert verdict(_result(pv_ok=False), _th())["outcome"] == "nonfirm"
    # the house's own fuse comes first: a person looks at it, however the grid answers
    v = verdict(_result(pv_ok=True, fuse=False), _th())
    assert v["outcome"] == "review" and v["reasons"][0]["code"] == "fuse"


def test_review_when_the_p90_exceeds_its_threshold():
    from kapacitet.engine.rules import verdict

    v = verdict(_result(mean=2.0, p90=4.0), _th(mean=3.0, p90=3.0))
    assert v["outcome"] == "review"
    assert [r["code"] for r in v["reasons"]] == ["curtailment_p90"]
    assert verdict(_result(mean=2.0, p90=4.0), _th(mean=3.0, p90=5.0))["outcome"] == "nonfirm"
    # unserved charging, unserved heat and a violation left each send it to a person
    assert verdict(_result(ev=False, ev_unmet=2.5), _th(ev=1.0))["reasons"][0]["code"] == "ev_unserved"
    assert verdict(_result(hp=False, hp_unmet=0.1), _th())["reasons"][0]["code"] == "hp_unserved"
    assert verdict(_result(left=3), _th())["reasons"][0]["code"] == "violations"


def test_the_rule_never_declines():
    from kapacitet.engine import queue as qu
    from kapacitet.engine.rules import OUTCOMES, verdict

    rng = random.Random(3)
    for _ in range(500):
        res = _result(pv_ok=rng.random() < 0.3, ev=rng.choice([None, True, False]), hp=rng.choice([None, True, False]),
                      mean=rng.uniform(0, 10), p90=rng.uniform(0, 15), ev_unmet=rng.uniform(0, 3),
                      hp_unmet=rng.choice([0.0, rng.uniform(0, 2)]), fuse=rng.random() < 0.9, left=rng.choice([0, 0, 2]))
        assert verdict(res, _th(*(rng.uniform(1, 5) for _ in range(3))))["outcome"] in OUTCOMES
    with pytest.raises(ValueError):
        qu.decide(None, None, None, {}, "declined", by="rule")


# ---------------------------------------------------------------- the indicators (no data)

def _ev(seq, at, app, event, **kw):
    return {"seq": seq, "at": f"2026-11-02T10:{at // 60:02d}:{at % 60:02d}+00:00", "app": app, "event": event, **kw}


def test_the_indicators_come_from_the_events():
    from kapacitet.api.inbox import fold, kpis

    evs = [
        _ev(1, 0, "a", "received", cid=1, assessment="x1", reference="KP-1"),
        _ev(2, 30, "a", "decided", outcome="nonfirm", by="rule", pv_kw=8.0),
        _ev(3, 60, "b", "received", cid=2, assessment="x2", reference="KP-2"),
        _ev(4, 90, "b", "review", reasons=[{"code": "curtailment_p90"}]),
        _ev(5, 1800, "b", "decided", outcome="firm", by="dso", pv_kw=5.0),
        _ev(6, 1900, "c", "received", cid=3, assessment="x3", reference="KP-3"),
    ]
    apps = fold(evs)
    assert [a["status"] for a in apps.values()] == ["nonfirm", "firm", "pending"]
    k = kpis(apps)
    assert k["received"] == 3 and k["decided"] == 2 and k["pending"] == 1 and k["decided_by_rule"] == 1
    assert k["share_by_rule_pct"] == pytest.approx(50.0)
    assert k["median_s"] == pytest.approx((30 + 1740) / 2)
    assert k["p90_s"] == pytest.approx(30 + 0.9 * (1740 - 30))
    assert k["firm_kw"] == pytest.approx(5.0) and k["nonfirm_kw"] == pytest.approx(8.0)


# ---------------------------------------------------------------- the worker, with a fake engine (no data)

@pytest.fixture()
def inbox_dirs(tmp_path, monkeypatch):
    from kapacitet import dso
    from kapacitet.api import inbox, records
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "requests" / "queue")
    monkeypatch.setattr(inbox, "log_path", lambda: tmp_path / "requests" / "applications.jsonl")
    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    return inbox, records, qu


def _sign(**over):
    """Automatic decisions on, under a rule signed for every kind of request (advisor mode is the default)."""
    from kapacitet import dso

    dso.update(dso.DsoSettingsChange(auto_decisions=True))
    body = {"signed_by": "A. Planner", "role": "network planning manager", "request_kinds": ["pv", "ev", "hp", "battery"],
            "max_pv_kwp": 40.0, "valid_until": "2099-12-31", **over}
    return dso.sign_rule(dso.RuleSignatureIn(**body))


def _saved(records, job_id, cid, baseline="static", pv_kwp=10.0, **result):
    rec = {"id": job_id, "created": "2026-11-02T10:00:00+00:00",
           "request": {"cid": cid, "pv_kwp": pv_kwp, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0,
                       "baseline": baseline},
           "result": {"customer": {"address": f"Gatan {cid}"}, **result}}
    records.save(rec)
    return rec


def test_an_old_assessment_is_evaluated_again_before_the_rule_decides(inbox_dirs, monkeypatch):
    inbox, records, qu = inbox_dirs
    _sign()
    # the queue already holds an approval, so an assessment made against today's grid is out of date
    qu._save({"version": 1, "baseline": "static", "items": [{"assessment": "f00000000000", "cid": 5, "decision": "nonfirm"}]})
    old = _saved(records, "a00000000001", cid=7, **_result(pv_ok=True))
    app = inbox.receive(old["id"])
    assert app["status"] == "pending" and app["reference"].startswith("KP-") and app["reference_hint"] == app["reference"][3:7]

    asked = []

    def evaluate(request):
        asked.append(request)
        return {**_result(pv_ok=False, mean=1.0, p90=2.0), "customer": {"address": "Gatan 7"}, "queue": {"version": 1}}

    def make_record(job_id, created, request, result):
        return {"id": job_id, "created": "2026-11-02T10:05:00+00:00", "request": request, "result": result}

    decided = []

    def fake_decide(model, hp, ev, record, decision, note="", emit=None, by="dso", signature=None, **kw):
        assert by != "rule" or signature                      # the rule's decision keeps its signature
        decided.append((record["id"], decision, by))
        return {"pv_kw": 9.5}

    monkeypatch.setattr(qu, "decide", fake_decide)
    inbox.process(app, engine=lambda: {"model": None, "hp": None, "ev": None}, make_record=make_record, evaluate=evaluate)

    assert asked and asked[0]["baseline"] == "queue"             # evaluated against the queue as it stands
    kinds = [e["event"] for e in inbox.events()]
    assert kinds == ["received", "reevaluated", "decided"]
    done = inbox.applications()[app["id"]]
    assert done["status"] == "nonfirm" and done["by"] == "rule" and done["pv_kw"] == pytest.approx(9.5)
    assert decided == [(done["assessment"], "nonfirm", "rule")] and done["assessment"] != old["id"]
    assert done["evaluations"] == 2


def test_a_verdict_that_needs_a_person_waits_and_one_application_per_house(inbox_dirs):
    inbox, records, qu = inbox_dirs
    _sign()
    rec = _saved(records, "a00000000002", cid=9, **_result(pv_ok=False, mean=5.0, p90=9.0))
    app = inbox.receive(rec["id"])
    inbox.process(app, engine=lambda: None, make_record=lambda *a: None, evaluate=lambda r: None)
    waiting = inbox.applications()[app["id"]]
    assert waiting["status"] == "review"
    assert {r["code"] for r in waiting["reasons"]} == {"curtailment_mean", "curtailment_p90"}
    # the same house cannot apply twice while it waits, and the same answer cannot back two applications
    other = _saved(records, "a00000000003", cid=9, **_result(pv_ok=True))
    with pytest.raises(Exception) as e:
        inbox.receive(other["id"])
    assert e.value.detail["code"] == "application_open"
    with pytest.raises(Exception) as e:
        inbox.receive(rec["id"])
    assert e.value.detail["code"] == "already_applied"


def test_decisions_share_the_queue_lock(inbox_dirs, monkeypatch):
    inbox, records, qu = inbox_dirs
    _sign()
    rec = _saved(records, "a00000000004", cid=11, **_result(pv_ok=True))
    app = inbox.receive(rec["id"])
    monkeypatch.setattr(qu, "decide", lambda *a, **k: {"pv_kw": 4.0})

    qu._LOCK.acquire()                       # a manual decision is being written
    try:
        t = threading.Thread(target=inbox.process, args=(app,),
                             kwargs={"engine": lambda: {"model": None, "hp": None, "ev": None},
                                     "make_record": lambda *a: None, "evaluate": lambda r: None})
        t.start()
        time.sleep(0.3)
        assert [e["event"] for e in inbox.events()] == ["received"]     # the worker waits for it
    finally:
        qu._LOCK.release()
    t.join(5)
    assert inbox.applications()[app["id"]]["status"] == "firm"

    from kapacitet.api import main

    inbox._STATE["working"] = app["id"]
    try:
        assert main._queue_busy()            # and a manual decision waits for the worker
    finally:
        inbox._STATE["working"] = None


def test_the_log_keeps_the_house_and_never_its_address(inbox_dirs, monkeypatch, tmp_path):
    from kapacitet import seals

    inbox, records, qu = inbox_dirs
    _sign()
    monkeypatch.setattr(records, "address_of", lambda cid: f"Gatan {cid}")
    rec = _saved(records, "a00000000005", cid=13, **_result(pv_ok=True))
    app = inbox.receive(rec["id"])
    assert app["address"] == "Gatan 13"                          # read from the network for the reader
    monkeypatch.setattr(qu, "decide", lambda *a, **k: {"pv_kw": 4.0})
    inbox.process(app, engine=lambda: {"model": None, "hp": None, "ev": None},
                  make_record=lambda *a: None, evaluate=lambda r: None)
    assert inbox.applications()[app["id"]]["status"] == "firm"
    text = inbox.log_path().read_text(encoding="utf-8")
    assert '"cid": 13' in text and "Gatan" not in text
    for path in seals.folder().rglob("*.json*"):
        assert "Gatan" not in path.read_text(encoding="utf-8"), path.name


# ---------------------------------------------------------------- a real automatic approval (data)

@needs_results
def test_an_automatic_approval_verifies_every_year(inbox_dirs):
    from kapacitet.api import main
    from kapacitet.config import settings

    inbox, records, qu = inbox_dirs
    sig = _sign()
    request = {"cid": 84, "pv_kwp": 4.0, "ev_kw": 0.0, "hp": False, "bat_kwh": 0.0, "ev_phases": 0, "baseline": "static"}
    rec = main.make_record("c00000000001", time.time(), request, main.evaluate_record(request))
    records.save(rec)
    app = inbox.receive(rec["id"])
    inbox.process(app)
    done = inbox.applications()[app["id"]]
    assert done["status"] in ("firm", "nonfirm"), done["reasons"]          # a small system: no person needed
    assert done["by"] == "rule"
    item = next(it for it in qu.load()["items"] if it["assessment"] == done["assessment"])
    assert item["by"] == "rule"
    from kapacitet import dso

    assert item["rule_signature"] == dso.signature_digest(sig.rule_signature)       # what it was decided under
    assert len(item["verification"]["years_verified"]) == len(settings().weather_years)
    assert item["verification"]["all_years"]["export_violations_after"] == 0


# ---------------------------------------------------------------- advisor mode

def _decide_spy(monkeypatch, qu):
    calls = []

    def decide(model, hp, ev, record, decision, note="", emit=None, by="dso", signature=None, **kw):
        calls.append({"decision": decision, "by": by, "signature": signature})
        return {"pv_kw": 4.0}

    monkeypatch.setattr(qu, "decide", decide)
    return calls


def _process(inbox, app):
    inbox.process(app, engine=lambda: {"model": None, "hp": None, "ev": None},
                  make_record=lambda *a: None, evaluate=lambda r: None)
    return inbox.applications()[app["id"]]


def test_the_defaults_never_decide_on_their_own(inbox_dirs, monkeypatch):
    from kapacitet import dso

    inbox, records, qu = inbox_dirs
    calls = _decide_spy(monkeypatch, qu)
    assert dso.load().auto_decisions is False and dso.load().rule_signature is None
    rec = _saved(records, "a00000000011", cid=21, **_result(pv_ok=True))
    done = _process(inbox, inbox.receive(rec["id"]))
    assert done["status"] == "review" and [r["code"] for r in done["reasons"]] == ["auto_off"]
    # in advisor mode the rule only proposes: its verdict is kept beside the application
    assert done["proposal"] == "firm" and done["proposal_reasons"] == []
    assert inbox.signature_state(dso.load()) == "none"
    # switched on, still nobody signed the rule: a person decides
    dso.update(dso.DsoSettingsChange(auto_decisions=True))
    rec = _saved(records, "a00000000012", cid=22, **_result(pv_ok=True))
    done = _process(inbox, inbox.receive(rec["id"]))
    assert done["status"] == "review" and [r["code"] for r in done["reasons"]] == ["unsigned_rule"]
    assert calls == []


def test_a_signed_rule_approves_through_the_queue_and_only_what_it_covers(inbox_dirs, monkeypatch):
    from kapacitet import dso, seals

    inbox, records, qu = inbox_dirs
    calls = _decide_spy(monkeypatch, qu)
    signed = _sign(request_kinds=["pv"], max_pv_kwp=8.0)
    digest = dso.signature_digest(signed.rule_signature)
    entries = [e for e in seals._lines() if e["kind"] == "rule_signature"]
    assert len(entries) == 1 and entries[0]["digest"] == digest

    small = _saved(records, "a00000000013", cid=23, pv_kwp=6.0, **_result(pv_ok=True))
    assert _process(inbox, inbox.receive(small["id"]))["status"] == "firm"
    assert calls == [{"decision": "firm", "by": "rule", "signature": digest}]

    big = _saved(records, "a00000000014", cid=24, **_result(pv_ok=True))        # 10 kWp, above the 8 signed
    done = _process(inbox, inbox.receive(big["id"]))
    assert done["status"] == "review" and [r["code"] for r in done["reasons"]] == ["out_of_scope"]
    # other thresholds than the signed ones are another rule, which nobody signed
    dso.update(dso.DsoSettingsChange(auto_curtailment_mean_pct=9.0))
    again = _saved(records, "a00000000015", cid=25, pv_kwp=6.0, **_result(pv_ok=True))
    done = _process(inbox, inbox.receive(again["id"]))
    assert done["status"] == "review" and [r["code"] for r in done["reasons"]] == ["unsigned_rule"]
    assert done["proposal"] == "firm"
    assert len(calls) == 1
    assert inbox.signature_state(dso.load()) == "thresholds_changed"


def test_the_inbox_says_whether_the_signed_rule_is_in_force(inbox_dirs):
    from datetime import date

    from kapacitet import dso

    inbox, _, _ = inbox_dirs
    signed = _sign(valid_until="2030-06-30")
    assert inbox.signature_state(signed, today=date(2030, 6, 30)) == "in_force"
    assert inbox.signature_state(signed, today=date(2030, 7, 1)) == "expired"
    body = inbox.get_inbox(limit=10, offset=0)
    assert body["rule_signature"]["signed_by"] == "A. Planner" and body["signature_state"] in ("in_force", "expired")
    assert body["auto_decisions"] is True
    dso.update(dso.DsoSettingsChange(auto_decisions=False))
    assert inbox.get_inbox(limit=10, offset=0)["auto_decisions"] is False


def test_the_rule_cannot_decide_without_a_signature_and_a_planner_names_themselves(inbox_dirs):
    inbox, records, qu = inbox_dirs
    rec = _saved(records, "a00000000016", cid=26, **_result(pv_ok=True))
    with pytest.raises(ValueError, match="signed rule"):
        qu.decide(None, None, None, rec, "firm", by="rule")
    item = qu.decide(None, None, None, rec, "declined", note="no", decided_by="  B. Engineer, planning  ")
    assert item["decided_by"] == "B. Engineer, planning" and item["by"] == "dso"
    assert qu.load()["items"][0]["decided_by"] == "B. Engineer, planning"
