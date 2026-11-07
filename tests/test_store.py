"""The server's state on both backends (kapacitet.store): files, and PostgreSQL when
KAPACITET_TEST_DATABASE_URL names a test database (skipped otherwise).

Each test runs once per backend through the same public functions the API calls: the records and
their index, the applications log, the DSO's settings, the seal chain, the market alternatives and
the queue, whose concurrent decisions must never both reserve the same headroom. Then the parts
only the database has: migrations applied twice change nothing, the chain refuses an update, and a
folder imported and exported again comes back byte for byte.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import fresh_test_schema, tiny_grid, use_database


@pytest.fixture(params=["files", "postgres"])
def backend(request, tmp_path, monkeypatch):
    """The state of a data folder of the test's own, on one backend."""
    from kapacitet import dso, market, seals, store
    from kapacitet.api import inbox, records
    from kapacitet.engine import queue as qu

    requests = tmp_path / "requests"
    monkeypatch.setattr(records, "folder", lambda: requests)
    monkeypatch.setattr(inbox, "log_path", lambda: requests / "applications.jsonl")
    monkeypatch.setattr(dso, "path", lambda: requests / "settings.json")
    monkeypatch.setattr(seals, "folder", lambda: requests)
    monkeypatch.setattr(market, "log_path", lambda: requests / "market_alternatives.jsonl")
    monkeypatch.setattr(qu, "folder", lambda: requests / "queue")
    if request.param == "postgres":
        use_database(monkeypatch, fresh_test_schema())
    else:
        monkeypatch.delenv("KAPACITET_DATABASE_URL", raising=False)
    assert store.backend() == request.param
    yield request.param
    from kapacitet import cache

    cache.clear_all()


def _record(job_id: str, cid: int, created: datetime | None = None) -> dict:
    from kapacitet.api import records

    when = (created or datetime.now(timezone.utc)).timestamp()
    return records.make_record(job_id, when, {"cid": cid, "pv_kwp": 6.0, "baseline": "static"},
                               {"summary": {"verdict": "nonfirm"}, "customer": {"cid": cid, "address": "Gatan 1"},
                                "seconds": 0.1})


# ---------------------------------------------------------------- records

def test_records_are_kept_listed_newest_first_and_purged_after_their_time(backend):
    from kapacitet import seals
    from kapacitet.api import records

    old = datetime.now(timezone.utc) - timedelta(days=40)
    records.save(_record("a00000000001", 3, created=old))
    records.save(_record("a00000000002", 4))
    got = records.load("a00000000002", resolve=False)
    assert got["request"]["cid"] == 4 and "address" not in got["result"]["customer"]   # never kept
    assert [it["id"] for it in records.listing()] == ["a00000000002", "a00000000001"]
    assert seals.verify("assessment", "a00000000002", got)["ok"]

    out = records.purge(30)
    assert out["ids"] == ["a00000000001"]
    assert records.load("a00000000001") is None and [it["id"] for it in records.listing()] == ["a00000000002"]
    assert seals.verify("assessment", "a00000000001")["erased"]
    assert seals.verify_chain()["ok"]


# ---------------------------------------------------------------- the applications log

def test_the_applications_log_numbers_its_events_and_an_assessment_backs_one_application(backend):
    from fastapi import HTTPException

    from kapacitet.api import inbox, records

    records.save(_record("b00000000001", 7))
    first = inbox.receive("b00000000001", key="k" * 24)
    again = inbox.receive("b00000000001", key="k" * 24)          # the same key: the same application
    assert again["id"] == first["id"] and again["reference"] == first["reference"]
    with pytest.raises(HTTPException) as refused:
        inbox.receive("b00000000001")
    assert refused.value.status_code == 409
    inbox.append({"app": first["id"], "event": "review", "reasons": []})
    assert [e["seq"] for e in inbox.events()] == [1, 2]
    assert inbox.applications()[first["id"]]["status"] == "review"
    assert first["reference"] not in json.dumps(inbox.events())          # only its digest is kept


def test_concurrent_appends_get_distinct_numbers(backend):
    from kapacitet.api import inbox

    def add(i):
        for j in range(5):
            inbox.append({"app": f"a{i}", "event": "note", "n": j})

    threads = [threading.Thread(target=add, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(e["seq"] for e in inbox.events()) == list(range(1, 21))


# ---------------------------------------------------------------- the DSO's settings

def test_settings_are_replaced_whole_and_every_change_is_sealed(backend):
    from kapacitet import dso, seals

    assert dso.load() == dso.DsoSettings()
    after = dso.update(dso.DsoSettingsChange(auto_decisions=True))
    assert after.auto_decisions and dso.load().auto_decisions
    assert dso.update(dso.DsoSettingsChange(auto_decisions=True)) == after       # no change: no seal
    changes = seals.sealed_of_kind("settings")
    assert len(changes) == 1 and changes[0]["record"]["after"]["auto_decisions"] is True


# ---------------------------------------------------------------- the seal chain

def test_the_chain_is_signed_verified_and_an_erased_copy_leaves_it_whole(backend):
    from kapacitet import seals

    for i in range(3):
        seals.seal("decision", f"d{i:011d}", {"decision": "firm", "i": i})
    chain = seals.verify_chain()
    assert chain["ok"] and chain["length"] == 3 and chain["signed"] == 3
    assert seals.sealed_copy("decision", "d00000000001") == {"decision": "firm", "i": 1}
    with pytest.raises(ValueError, match="different content"):
        seals.seal("decision", "d00000000001", {"decision": "nonfirm"})
    seals.erase("decision", "d00000000001", "a request to delete it")
    assert seals.sealed_copy("decision", "d00000000001") is None
    out = seals.verify("decision", "d00000000001")
    assert out["erased"] and out["ok"] and seals.verify_chain()["length"] == 4
    assert [e["entry"]["id"] for e in seals.sealed_of_kind("decision")] == ["d00000000000", "d00000000002"]


def test_concurrent_seals_make_one_unbroken_chain(backend):
    from kapacitet import seals

    def add(i):
        for j in range(4):
            seals.seal("decision", f"c{i}{j:010d}", {"i": i, "j": j})

    threads = [threading.Thread(target=add, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chain = seals.verify_chain()
    assert chain["ok"] and chain["length"] == 12


# ---------------------------------------------------------------- the market alternatives

def _market_body():
    from kapacitet import market

    return market.RecordIn.model_validate({
        "scope": "activation_period", "ref": {"start": "2026-06-01", "end": "2026-06-30"},
        "alternatives": [{"kind": "local_flexibility_market", "name": "Lokal marknad", "consulted_at": "2026-05-20",
                          "outcome": "no bids"}],
        "decided_by": "Planner"})


def test_a_market_record_is_sealed_once_under_its_key(backend):
    from kapacitet import market

    kept, seal = market.record(_market_body(), idempotency_key="key-0123456789abcdef")
    again, seal2 = market.record(_market_body(), idempotency_key="key-0123456789abcdef")
    assert again == kept and seal2["seq"] == seal["seq"]
    market.record(_market_body())
    listed = market.with_seals()
    assert len(listed) == 2 and listed[-1]["id"] == kept["id"] and listed[-1]["seal"]["digest"] == seal["digest"]


# ---------------------------------------------------------------- the queue (tiny grid)

@pytest.fixture()
def tiny_queue(backend, tiny_processed, tmp_path):
    import shutil

    from kapacitet.config import settings
    from kapacitet.engine import request as rq
    from kapacitet.engine.run import background_masks

    folder = tmp_path / "data" / "processed"
    shutil.copytree(tiny_processed, folder)
    with tiny_grid(folder):
        m = rq.model_at(0.98)
        hp, ev = background_masks(m.C)
        name = rq.band_name("existing", 0.98)
        rq.precompute(m, name, settings().weather_years, hp, ev)
        yield m, hp, ev, name, tmp_path / "data" / "requests" / "queue_mv098"


def _assessment(tq, job_id: str, cid: int, pv: float, queue: bool = False) -> dict:
    from kapacitet.engine import queue as qu
    from kapacitet.engine import request as rq

    m, hp, ev, name, where = tq
    ctx = qu.context(where) if queue else None
    r = rq.Request(cid=cid, pv_kwp=pv, baseline="queue" if queue else name)
    out = rq.evaluate(m, r, hp, ev, ctx=ctx)
    return {"id": job_id, "request": dict(r.__dict__), "result": json.loads(json.dumps(out, default=float))}


def test_an_approval_moves_the_queue_and_concurrent_decisions_cannot_over_reserve(tiny_queue, monkeypatch):
    from kapacitet import seals
    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    first = _assessment(tiny_queue, "e00000000001", m.C - 1, 4.0)
    item = qu.decide(m, hp, ev, first, "nonfirm", where=where)
    q = qu.load(where)
    assert item["decision"] == "nonfirm" and q["version"] == 1 and len(qu.approved(q)) == 1
    assert seals.entry_for("decision", first["id"])["digest"] == seals.digest(item)
    ctx = qu.context(where)
    assert set(ctx.overrides) == {m.C - 1} and ctx.states and ctx.label["version"] == 1

    # two answers against version 1, decided at the same moment: one is taken, the other must be
    # evaluated again, so the headroom of version 1 is never handed out twice
    a = _assessment(tiny_queue, "e00000000002", m.C - 2, 3.0, queue=True)
    b = _assessment(tiny_queue, "e00000000003", m.C - 3, 3.0, queue=True)
    real = qu._reserve

    def slow(*args, **kw):
        time.sleep(0.5)                       # both are inside decide before either commits
        return real(*args, **kw)

    monkeypatch.setattr(qu, "_reserve", slow)
    start = threading.Barrier(2)
    done: dict[str, object] = {}

    def run(rec):
        start.wait()
        try:
            done[rec["id"]] = qu.decide(m, hp, ev, rec, "nonfirm", where=where)
        except qu.QueueError as exc:
            done[rec["id"]] = exc

    threads = [threading.Thread(target=run, args=(r,)) for r in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    refused = [v for v in done.values() if isinstance(v, qu.QueueError)]
    assert len(refused) == 1 and refused[0].code == "queue_changed"
    q = qu.load(where)
    assert q["version"] == 2 and len(qu.approved(q)) == 2 and len(q["items"]) == 2
    # the version left behind is the only one kept
    ctx = qu.context(where)
    assert ctx.label["version"] == 2 and len(ctx.overrides) == 2

    qu.reset(where)
    assert qu.load(where)["items"] == [] and len(seals.sealed_of_kind("queue_reset")) == 1


def test_a_decision_whose_seal_failed_is_sealed_by_recover(tiny_queue, monkeypatch):
    from kapacitet import seals
    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    rec = _assessment(tiny_queue, "f00000000001", m.C - 1, 4.0)
    real = seals.seal
    monkeypatch.setattr(seals, "seal", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        qu.decide(m, hp, ev, rec, "declined", where=where)
    monkeypatch.setattr(seals, "seal", real)
    assert qu.load(where)["items"][0]["assessment"] == rec["id"]          # committed: the seal is owed
    assert seals.entry_for("decision", rec["id"]) is None
    assert qu.recover(where)["sealed"] == rec["id"]
    assert seals.entry_for("decision", rec["id"]) is not None and qu.recover(where)["sealed"] is None


def test_a_failure_while_the_states_are_written_leaves_the_queue_as_it_was(tiny_queue, monkeypatch):
    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    qu.decide(m, hp, ev, _assessment(tiny_queue, "ab0000000001", m.C - 1, 4.0), "nonfirm", where=where)
    before = qu.load(where)
    rec = _assessment(tiny_queue, "ab0000000002", m.C - 2, 3.0, queue=True)
    kind = type(qu._store(where))
    real, calls = kind.put_npz, []

    def disk_full_at_the_third(self, file, arrays, stable=False):
        calls.append(file)
        if len(calls) == 3:
            raise OSError("disk full")
        return real(self, file, arrays, stable)

    monkeypatch.setattr(kind, "put_npz", disk_full_at_the_third)
    with pytest.raises(OSError, match="disk full"):
        qu.decide(m, hp, ev, rec, "nonfirm", where=where)
    monkeypatch.setattr(kind, "put_npz", real)
    assert qu.load(where) == before
    qu.recover(where)
    left = qu._store(where).files()
    assert not [f for f in left if f.endswith(f".v{before['version'] + 1}.npz")] and f"{rec['id']}.npz" not in left
    assert qu.context(where).label["version"] == before["version"]


def test_a_rebuild_gives_the_same_queue_on_either_backend(tiny_queue):
    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    items = [{"assessment": "0000000000a1", "cid": m.C - 1, "decision": "nonfirm", "decided": "2026-08-01",
              "request": {"cid": m.C - 1, "pv_kwp": 8.0}}]
    q = qu.rebuild(m, hp, ev, name, items, where=where)
    assert q["version"] == 1 and qu.load(where) == q
    assert set(qu.context(where).overrides) == {m.C - 1}
    qu.rebuild(m, hp, ev, name, [], where=where)
    assert qu.load(where)["version"] == 0 and qu.context(where).overrides == {}


# ---------------------------------------------------------------- only the database

@pytest.fixture()
def database(monkeypatch):
    url = fresh_test_schema()
    use_database(monkeypatch, url)
    return url


def test_migrate_is_idempotent_and_status_says_so(database):
    from kapacitet.store import migrate

    assert migrate.migrate(database) == []                 # the session already applied them
    out = migrate.status(database)
    assert out["pending"] == [] and out["changed"] == [] and all(r["applied"] for r in out["migrations"])
    assert migrate.migrate(database) == []


def test_the_database_chain_refuses_an_update_and_a_changed_copy_shows(database, monkeypatch, tmp_path):
    import psycopg

    from kapacitet import seals, store

    monkeypatch.setattr(seals, "folder", lambda: tmp_path / "nowhere")
    seals.seal("decision", "d00000000001", {"decision": "firm"})
    with psycopg.connect(database, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE seal_chain SET digest = 'x' WHERE scope = %s", (store.scope(),))
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM seal_chain WHERE scope = %s", (store.scope(),))
        conn.execute("UPDATE sealed_copies SET body = %s WHERE scope = %s", (b'{"decision":"nonfirm"}', store.scope()))
    out = seals.verify("decision", "d00000000001")
    assert out["chain_ok"] and not out["copy_ok"] and not out["ok"]
    assert not (tmp_path / "nowhere").exists()                    # nothing went to files


def _fill_files(tmp_path, monkeypatch) -> Path:
    """A data/requests folder written by the files backend: every kind of state there is."""
    import numpy as np

    from kapacitet import dso, market, seals
    from kapacitet.api import inbox, records
    from kapacitet.atomic import savez_atomic, write_json_atomic
    from kapacitet.engine import queue as qu

    requests = tmp_path / "requests"
    monkeypatch.setattr(records, "folder", lambda: requests)
    monkeypatch.setattr(inbox, "log_path", lambda: requests / "applications.jsonl")
    monkeypatch.setattr(dso, "path", lambda: requests / "settings.json")
    monkeypatch.setattr(seals, "folder", lambda: requests)
    monkeypatch.setattr(market, "log_path", lambda: requests / "market_alternatives.jsonl")
    monkeypatch.setattr(qu, "folder", lambda: requests / "queue")
    monkeypatch.delenv("KAPACITET_DATABASE_URL", raising=False)
    records.save(_record("c00000000001", 3))
    records.save(_record("c00000000002", 4))
    inbox.receive("c00000000001")
    dso.update(dso.DsoSettingsChange(auto_decisions=True))
    market.record(_market_body())
    q = {"version": 1, "baseline": "static", "items": [{"assessment": "c00000000002", "cid": 4, "decision": "nonfirm"}]}
    folder = requests / "queue"
    savez_atomic(folder / "c00000000002.npz", p_2025=np.arange(4, dtype=np.float32))
    savez_atomic(folder / "state_2025.v1.npz", stable=True, pt=np.ones(3, dtype=np.uint8))
    write_json_atomic(folder / "queue.json", q)
    write_json_atomic(folder / qu.PENDING, {"kind": "decision", "id": "c00000000002"})
    return requests


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_import_then_export_round_trips_byte_for_byte_and_import_is_idempotent(tmp_path, monkeypatch):
    from kapacitet import seals
    from kapacitet.api import records
    from kapacitet.store import transfer

    requests = _fill_files(tmp_path, monkeypatch)
    before = _tree(requests)
    url = fresh_test_schema()
    use_database(monkeypatch, url)
    first = transfer.import_files(requests)
    assert first["records"] == 2 and first["applications"] == 1 and first["settings"] == "imported"
    assert first["seal_lines"] == len((requests / "seals.jsonl").read_text(encoding="utf-8").splitlines())
    assert first["queues"] == {"queue": "imported"} and not first["conflicts"]
    again = transfer.import_files(requests)
    assert again["records"] == again["applications"] == again["seal_lines"] == again["sealed_copies"] == 0
    assert again["settings"] == "same" and again["queues"] == {"queue": "same"}

    # the database now serves what the files held
    assert records.load("c00000000001", resolve=False) == json.loads(
        gzip.decompress(before["c00000000001.json.gz"]).decode("utf-8"))
    assert seals.verify_chain()["ok"]

    out = tmp_path / "exported"
    transfer.export_files(out)
    assert _tree(out) == before
    with pytest.raises(transfer.TransferError, match="not empty"):
        transfer.export_files(out)


def test_import_refuses_a_chain_that_differs_from_the_database(tmp_path, monkeypatch):
    from kapacitet import seals
    from kapacitet.store import transfer

    requests = _fill_files(tmp_path, monkeypatch)
    use_database(monkeypatch, fresh_test_schema())
    seals.seal("decision", "d00000000009", {"decision": "firm"})      # the database's chain starts elsewhere
    with pytest.raises(transfer.TransferError, match="differ at line 1"):
        transfer.import_files(requests)


def test_the_command_line_migrates_imports_and_exports(tmp_path, monkeypatch, capsys):
    from kapacitet import pipeline

    requests = _fill_files(tmp_path, monkeypatch)
    url = fresh_test_schema()
    scope = use_database(monkeypatch, url)            # restored after the test, though `--url` sets it again
    pipeline.main(["db", "migrate", "--url", url])
    assert "up to date" in capsys.readouterr().out
    pipeline.main(["db", "status", "--url", url, "--scope", scope])
    assert scope in capsys.readouterr().out
    pipeline.main(["db", "import-files", str(requests), "--url", url, "--scope", scope])
    assert json.loads(capsys.readouterr().out)["records"] == 2
    pipeline.main(["db", "export-files", str(tmp_path / "out"), "--url", url, "--scope", scope])
    capsys.readouterr()
    assert _tree(tmp_path / "out") == _tree(requests)
    pipeline.main(["seals", "verify"])                                  # the database's own chain
    assert "valid" in capsys.readouterr().out


def test_nothing_kept_holds_an_address(backend, tmp_path):
    """The record is kept without the house's address and a decision keeps only its number, on
    either backend: every byte kept (exported, for the database) is searched for it."""
    from kapacitet.api import records
    from kapacitet.engine import queue as qu
    from kapacitet.store import transfer

    records.save(_record("d00000000001", 5))
    rec = records.load("d00000000001", resolve=False)
    item = qu.decide(None, None, None, rec, "declined", "no room this year")
    assert "address" not in item and "address" not in rec["result"]["customer"]
    if backend == "postgres":
        kept = tmp_path / "kept"
        transfer.export_files(kept)
    else:
        kept = records.folder()
    written = [p for p in kept.rglob("*") if p.is_file()]
    assert any(p.name == "seals.jsonl" for p in written) and any(p.name == "queue.json" for p in written)
    for path in written:
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        assert b"Gatan" not in data, f"{path.relative_to(kept)} keeps the address"
