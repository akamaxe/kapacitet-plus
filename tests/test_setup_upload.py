"""The setup page's bundle upload (api/setup.py): closed unless allowed and empty, resumable, checked.

It runs on a fresh clone: the bundle is a tiny one made here, and every folder it touches is a
temporary one, so nothing in data/ is read or written. A server takes only the pinned release
unless it allows any bundle (KAPACITET_SETUP_ANY_BUNDLE=1): most tests allow any, as the interface's
own tests do, and the last ones pin the tiny bundle or leave it unpinned.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from kapacitet.bundle import make_bundle

API = "/api/v1/setup/upload"
FILES = {
    "processed/results/showcase.json": b'{"year": 2025}',
    "processed/results/meta.json": b"{}",
    "processed/ui/state_doe.bin": bytes(range(256)) * 64,
    "raw/manifest.json": b"{}",
}


@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def bundle_bytes(tmp_path):
    src = tmp_path / "src"
    for rel, body in FILES.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(body)
    out = tmp_path / "kapacitet-data.zip"
    make_bundle(out, data_dir=src)
    return out.read_bytes()


@pytest.fixture()
def opened(monkeypatch, tmp_path):
    """The upload allowed, on an empty copy whose folders are temporary; restarts are recorded."""
    from kapacitet import datasets as ds
    from kapacitet.api import main, records, setup

    restarts: list[float] = []
    monkeypatch.setenv("KAPACITET_SETUP_UPLOAD", "1")
    monkeypatch.setenv("KAPACITET_SETUP_ANY_BUNDLE", "1")
    monkeypatch.setenv("KAPACITET_SUPERVISED", "1")
    monkeypatch.setattr(records, "folder", lambda: tmp_path / "requests")
    monkeypatch.setattr(setup, "target", lambda: tmp_path / "data")
    monkeypatch.setattr(main, "results_ready", lambda: False)
    monkeypatch.setattr(ds, "restart_soon", lambda delay=0.5: restarts.append(delay))
    monkeypatch.setattr(setup, "_STATE", {"state": "idle", "files_done": 0, "files_total": 0, "error": None,
                                          "restart": None, "total": None})
    return tmp_path, restarts


def _put(client, chunk: bytes, offset: int, total: int, fp: str = "kapacitet-data.zip|1|1"):
    return client.put(API, params={"offset": offset, "total": total, "fp": fp}, content=chunk,
                      headers={"Content-Type": "application/octet-stream"})


def _wait(client, states=("done", "failed")) -> dict:
    for _ in range(200):
        st = client.get(API).json()
        if st["state"] in states:
            return st
        time.sleep(0.02)
    raise AssertionError(f"the upload never reached {states}: {st}")


def test_closed_unless_the_server_allows_it(client, monkeypatch):
    monkeypatch.delenv("KAPACITET_SETUP_UPLOAD", raising=False)
    assert client.get(API).json()["open"] is False
    r = _put(client, b"PK\x03\x04", 0, 10)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "setup_closed"
    assert client.get("/api/v1/health").json()["setup_upload"] is False


def test_a_copy_with_results_or_on_another_network_takes_no_bundle(client, opened, monkeypatch):
    from kapacitet import datasets as ds
    from kapacitet.api import main

    monkeypatch.setattr(main, "results_ready", lambda: True)
    r = _put(client, b"PK\x03\x04", 0, 10)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_computed"
    monkeypatch.setattr(main, "results_ready", lambda: False)
    monkeypatch.setattr(ds, "current", lambda: "my-network")
    r = _put(client, b"PK\x03\x04", 0, 10)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "other_network"


def test_open_on_an_empty_copy_and_health_says_so(client, opened):
    h = client.get("/api/v1/health").json()
    assert h["setup_upload"] is True and h["boot"]


def test_a_file_that_is_not_a_zip_is_refused_at_the_first_chunk(client, opened):
    r = _put(client, b"%PDF-1.7 ...", 0, 12)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_a_bundle"


def test_a_whole_upload_in_chunks_unpacks_and_restarts(client, opened, bundle_bytes):
    tmp_path, restarts = opened
    total = len(bundle_bytes)
    step = total // 5 + 1                   # five chunks, the last one short
    for offset in range(0, total, step):
        r = _put(client, bundle_bytes[offset:offset + step], offset, total)
        assert r.status_code == 200, r.text
    st = _wait(client)
    assert st["state"] == "done" and st["restart"] == "auto" and st["error"] is None
    assert st["files_done"] == st["files_total"] == len(FILES)
    assert st["received"] == st["total"] == total
    for rel, body in FILES.items():
        assert (tmp_path / "data" / rel).read_bytes() == body
    assert restarts, "the server was not handed back to its supervisor"
    assert not any((tmp_path / "requests" / "setup-upload").glob("bundle.zip*"))


def test_a_reloaded_page_resumes_where_the_upload_stopped(client, opened, bundle_bytes):
    total, half = len(bundle_bytes), len(bundle_bytes) // 2
    assert _put(client, bundle_bytes[:half], 0, total).status_code == 200
    st = client.get(API).json()
    assert st["state"] == "receiving" and st["received"] == half and st["total"] == total
    wrong = _put(client, bundle_bytes[half:], half + 10, total)
    assert wrong.status_code == 409 and wrong.json()["detail"] == {
        "code": "offset_mismatch", "message": f"the upload is at byte {half}", "params": {"received": half}}
    assert _put(client, bundle_bytes[half:], half, total).status_code == 200
    assert _wait(client)["state"] == "done"


def test_a_second_tab_with_another_file_waits_for_the_first(client, opened, bundle_bytes):
    total = len(bundle_bytes)
    assert _put(client, bundle_bytes[:1000], 0, total, fp="a.zip|1|1").status_code == 200
    r = _put(client, bundle_bytes[:1000], 0, total, fp="b.zip|2|2")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_busy"


def test_a_damaged_bundle_fails_with_its_code_and_does_not_restart(client, opened, bundle_bytes, tmp_path):
    _, restarts = opened
    src = tmp_path / "good.zip"
    src.write_bytes(bundle_bytes)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            body = zin.read(info)
            zout.writestr(info.filename, b"tampered" if info.filename == "data/raw/manifest.json" else body)
    body = bad.read_bytes()
    assert _put(client, body, 0, len(body)).status_code == 200
    st = _wait(client)
    assert st["state"] == "failed" and st["error"] == {"code": "damaged", "params": {"entry": "data/raw/manifest.json"}}
    assert not restarts


def test_a_chunk_beyond_the_cap_is_refused(client, opened, monkeypatch):
    from kapacitet.api import setup

    monkeypatch.setattr(setup, "CHUNK_MAX", 8)
    r = _put(client, b"PK\x03\x04" + b"x" * 20, 0, 24)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "chunk_too_large"


def test_a_quiet_progress_stream_sends_keepalive_comments():
    import asyncio

    from kapacitet.api import main

    jobs = {"j": {"events": [], "closed": False}}
    resp = main._stream(jobs, "j", timeout_s=5, keepalive_s=0.05)

    async def collect():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
            if len(out) == 2:
                jobs["j"]["closed"] = True
        return out

    chunks = asyncio.run(collect())
    assert chunks[0] == ": keepalive\n\n" and chunks[-1].startswith("event: end")


def test_a_page_reloaded_mid_chunk_leaves_nothing_and_no_traceback(opened):
    """The browser drops the request half-way: the server answers nothing useful and writes nothing."""
    import asyncio

    from starlette.requests import ClientDisconnect

    from kapacitet.api import setup

    class Gone:
        headers = {"content-length": "8388608"}

        async def body(self):
            raise ClientDisconnect()

    r = asyncio.run(setup.upload_chunk(Gone(), offset=0, total=100, fp="a.zip|1|1"))
    assert r.status_code == 400
    assert not setup._part().exists()


# ---------------------------------------------------------------- only the pinned release

@pytest.fixture()
def pin(monkeypatch, tmp_path):
    """Pinned mode (no KAPACITET_SETUP_ANY_BUNDLE), with config/data-release.json in a temporary file."""
    from kapacitet import bundle

    monkeypatch.delenv("KAPACITET_SETUP_ANY_BUNDLE", raising=False)
    path = tmp_path / "data-release.json"
    monkeypatch.setattr(bundle, "RELEASE", path)

    def write(body: bytes | None) -> None:
        if body is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps({"url": "https://example.org/kapacitet-data.zip",
                                        "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}),
                            encoding="utf-8")
    return write


def test_the_pinned_release_is_taken(client, opened, bundle_bytes, pin):
    pin(bundle_bytes)
    assert _put(client, bundle_bytes, 0, len(bundle_bytes)).status_code == 200
    assert _wait(client)["state"] == "done"


def test_a_file_of_another_size_than_the_pinned_one_is_refused_at_the_first_chunk(client, opened, bundle_bytes, pin):
    pin(bundle_bytes + b"!")
    r = _put(client, bundle_bytes[:1000], 0, len(bundle_bytes))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_pinned"
    assert client.get(API).json()["received"] == 0


def test_a_file_of_the_pinned_size_but_other_bytes_is_not_unpacked(client, opened, bundle_bytes, pin):
    tmp_path, restarts = opened
    pin(bundle_bytes)
    other = bundle_bytes[:-1] + bytes([bundle_bytes[-1] ^ 1])
    assert _put(client, other, 0, len(other)).status_code == 200
    st = _wait(client)
    assert st["state"] == "failed" and st["error"]["code"] == "not_pinned"
    assert not (tmp_path / "data").exists() or not any((tmp_path / "data").rglob("*"))
    assert not restarts


def test_without_a_pinned_release_no_file_is_taken(client, opened, bundle_bytes, pin):
    pin(None)
    r = _put(client, bundle_bytes, 0, len(bundle_bytes))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_pinned"
