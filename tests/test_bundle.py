"""Data bundles: what goes in, that it comes out identical, and that a bad archive is refused."""

from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from kapacitet.bundle import MANIFEST, BundleError, make_bundle, unpack_bundle


@pytest.fixture()
def data(tmp_path):
    root = tmp_path / "data"
    files = {
        "processed/results/showcase.json": b'{"year": 2025}',
        "processed/results/meta.json": b"{}",
        "processed/ui/state_doe.bin": bytes(range(256)) * 4,
        "processed/grid/lines.parquet": b"PAR1",
        "processed/pv/roofs.parquet": b"PAR1roofs",
        "processed/request/static_2025.npz": b"npz",
        "raw/manifest.json": b"{}",
        "raw/prices/elpris.jsonl": b"[]",
    }
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
    return root, files


def test_a_full_bundle_round_trips_byte_for_byte(data, tmp_path):
    root, files = data
    out = tmp_path / "b.zip"
    manifest = make_bundle(out, level="full", data_dir=root)
    assert set(manifest["files"]) == {f"data/{k}" for k in files}
    target = tmp_path / "elsewhere"
    r = unpack_bundle(out, data_dir=target, web_dir=tmp_path / "web")
    assert r["written"] == len(files) and r["level"] == "full"
    for rel, body in files.items():
        assert (target / rel).read_bytes() == body


def test_a_lite_bundle_leaves_out_what_only_live_requests_need(data, tmp_path):
    root, _ = data
    manifest = make_bundle(tmp_path / "lite.zip", level="lite", data_dir=root)
    names = set(manifest["files"])
    assert "data/processed/results/showcase.json" in names and "data/processed/pv/roofs.parquet" in names
    assert "data/processed/request/static_2025.npz" not in names
    assert "data/raw/prices/elpris.jsonl" not in names


def test_unpacking_keeps_existing_files_unless_forced(data, tmp_path):
    root, _ = data
    out = tmp_path / "b.zip"
    make_bundle(out, level="lite", data_dir=root)
    target = tmp_path / "t"
    (target / "processed" / "results").mkdir(parents=True)
    (target / "processed" / "results" / "meta.json").write_bytes(b"mine")
    r = unpack_bundle(out, data_dir=target, web_dir=tmp_path / "web")
    assert r["skipped"] == 1 and (target / "processed" / "results" / "meta.json").read_bytes() == b"mine"
    unpack_bundle(out, data_dir=target, web_dir=tmp_path / "web", force=True)
    assert (target / "processed" / "results" / "meta.json").read_bytes() == b"{}"


def test_nothing_to_bundle_and_damaged_or_hostile_archives_are_refused(tmp_path):
    with pytest.raises(BundleError, match="nothing computed"):
        make_bundle(tmp_path / "x.zip", data_dir=tmp_path / "empty")
    evil = tmp_path / "evil.zip"
    body = b"owned"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("data/../../escape.txt", body)
        z.writestr(MANIFEST, json.dumps({"kapacitet": "x", "created": "", "level": "full", "web": False,
                                         "files": {"data/../../escape.txt": hashlib.sha256(body).hexdigest()}}))
    with pytest.raises(BundleError, match="outside"):
        unpack_bundle(evil, data_dir=tmp_path / "d", web_dir=tmp_path / "w")
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("data/processed/x.json", b"changed")
        z.writestr(MANIFEST, json.dumps({"kapacitet": "x", "created": "", "level": "full", "web": False,
                                         "files": {"data/processed/x.json": hashlib.sha256(b"original").hexdigest()}}))
    with pytest.raises(BundleError, match="checksum"):
        unpack_bundle(bad, data_dir=tmp_path / "d2", web_dir=tmp_path / "w2")
