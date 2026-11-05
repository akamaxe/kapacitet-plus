"""Data bundles: what goes in, that it comes out identical, and that a bad archive is refused."""

from __future__ import annotations

import hashlib
import json
import os
import time
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


# ---------------------------------------------------------------- what an uploaded archive may do
# The setup page unpacks bundles uploaded from a browser (api/setup.py), so unpacking trusts nothing.

def _rezip(src, out, rename=lambda n: n, change=None, extra=()):
    """Copy an archive entry by entry, renaming, altering one member's bytes, or adding entries."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            body = zin.read(info)
            if change and info.filename == change[0]:
                body = change[1]
            zout.writestr(rename(info.filename), body)
        for name, body in extra:
            zout.writestr(name, body)
    return out


def _codes(fn):
    with pytest.raises(BundleError) as err:
        fn()
    return err.value.code


def _manifest(entries: dict[str, bytes]) -> bytes:
    return json.dumps({"kapacitet": "x", "created": "", "level": "full", "web": False,
                       "files": {k: hashlib.sha256(v).hexdigest() for k, v in entries.items()}}).encode()


def test_what_is_not_a_bundle_says_so(tmp_path):
    junk = tmp_path / "notes.zip"
    junk.write_bytes(b"%PDF-1.7 not a zip at all")
    assert _codes(lambda: unpack_bundle(junk, data_dir=tmp_path / "d")) == "not_a_bundle"
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("data/processed/x.json", b"{}")
    assert _codes(lambda: unpack_bundle(plain, data_dir=tmp_path / "d")) == "not_a_bundle"
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr(MANIFEST, b"{not json")
    assert _codes(lambda: unpack_bundle(broken, data_dir=tmp_path / "d")) == "not_a_bundle"


def test_a_damaged_member_leaves_no_partial_file_and_no_results(data, tmp_path):
    root, _ = data
    good = tmp_path / "good.zip"
    make_bundle(good, data_dir=root)
    bad = _rezip(good, tmp_path / "bad.zip", change=("data/processed/ui/state_doe.bin", b"tampered"))
    target = tmp_path / "t"
    err = pytest.raises(BundleError, unpack_bundle, bad, data_dir=target)
    assert err.value.code == "damaged" and err.value.params == {"entry": "data/processed/ui/state_doe.bin"}
    assert not list(target.rglob("*.part"))
    assert not (target / "processed" / "ui" / "state_doe.bin").exists()
    # the results only count once showcase.json is there, and it is written last
    assert not (target / "processed" / "results" / "showcase.json").exists()
    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(good) as zin, zipfile.ZipFile(missing, "w") as zout:
        for info in zin.infolist():
            if info.filename != "data/raw/manifest.json":
                zout.writestr(info, zin.read(info))
    assert _codes(lambda: unpack_bundle(missing, data_dir=tmp_path / "t2")) == "damaged"


@pytest.mark.parametrize("entry", ["data/requests/settings.json", "data/active.json", "data/datasets/x/dataset.json",
                                   "web/index.html", "README.md", "data/processed/../../evil.py",
                                   "data/processed/C:/x.json"])
def test_an_archive_writes_only_the_data_folders_and_the_web_build(tmp_path, entry):
    body = b"{}"
    z_path = tmp_path / "x.zip"
    with zipfile.ZipFile(z_path, "w") as z:
        z.writestr(entry, body)
        z.writestr(MANIFEST, _manifest({entry: body}))
    err = pytest.raises(BundleError, unpack_bundle, z_path, data_dir=tmp_path / "d", web_dir=tmp_path / "w")
    assert err.value.code == "unexpected_entry"
    assert not any((tmp_path / "d").rglob("*")) if (tmp_path / "d").exists() else True


def test_a_bundle_recompressed_inside_one_folder_still_unpacks(data, tmp_path):
    root, files = data
    good = tmp_path / "good.zip"
    make_bundle(good, data_dir=root)
    wrapped = _rezip(good, tmp_path / "wrapped.zip", rename=lambda n: "kapacitet-data/" + n,
                     extra=[("__MACOSX/kapacitet-data/._bundle-manifest.json", b"junk")])
    target = tmp_path / "t"
    r = unpack_bundle(wrapped, data_dir=target)
    assert r["written"] == len(files)
    assert all((target / rel).read_bytes() == body for rel, body in files.items())


def test_showcase_is_written_last_and_progress_counts_every_file(data, tmp_path, monkeypatch):
    import kapacitet.bundle as b

    root, files = data
    out = tmp_path / "b.zip"
    make_bundle(out, data_dir=root)
    order, seen = [], []
    real = b._extract
    monkeypatch.setattr(b, "_extract", lambda z, info, name, digest, dest, *rest: (order.append(name),
                                                                                     real(z, info, name, digest, dest, *rest)))
    unpack_bundle(out, data_dir=tmp_path / "t", progress=lambda done, total: seen.append((done, total)))
    assert order[-1] == b.LAST
    assert seen[-1] == (len(files), len(files)) and len(seen) == len(files)


def test_only_data_leaves_the_archives_web_build_out(tmp_path):
    entries = {"data/processed/results/showcase.json": b"{}", "web/dist/index.html": b"<html>"}
    z_path = tmp_path / "x.zip"
    with zipfile.ZipFile(z_path, "w") as z:
        for name, body in entries.items():
            z.writestr(name, body)
        z.writestr(MANIFEST, _manifest(entries))
    r = unpack_bundle(z_path, data_dir=tmp_path / "d", web_dir=tmp_path / "w", only_data=True)
    assert r["written"] == 1 and not (tmp_path / "w" / "index.html").exists()
    r = unpack_bundle(z_path, data_dir=tmp_path / "d2", web_dir=tmp_path / "w2")
    assert r["written"] == 2 and (tmp_path / "w2" / "index.html").read_bytes() == b"<html>"


def test_an_archive_that_unpacks_to_too_much_is_refused_before_writing(data, tmp_path, monkeypatch):
    import kapacitet.bundle as b

    root, _ = data
    out = tmp_path / "b.zip"
    make_bundle(out, data_dir=root)
    monkeypatch.setattr(b, "MAX_TOTAL", 100)
    assert _codes(lambda: unpack_bundle(out, data_dir=tmp_path / "t")) == "bundle_too_large"
    assert not (tmp_path / "t").exists() or not any((tmp_path / "t").rglob("*"))


# ---------------------------------------------------------------- modification times travel
# The API dates each result by its file's modification time, so unpacking must not reset them.

def test_a_round_trip_keeps_each_files_modification_time(data, tmp_path):
    root, files = data
    base = time.time() - 30 * 86400
    stamps = {rel: base + 3600 * i + 0.25 for i, rel in enumerate(sorted(files))}
    for rel, mt in stamps.items():
        os.utime(root / rel, (mt, mt))
    out = tmp_path / "b.zip"
    manifest = make_bundle(out, data_dir=root)
    assert set(manifest["mtimes"]) == set(manifest["files"])
    target = tmp_path / "elsewhere"
    unpack_bundle(out, data_dir=target, web_dir=tmp_path / "web")
    for rel, mt in stamps.items():
        assert abs((target / rel).stat().st_mtime - mt) < 2, rel


def test_a_bundle_made_before_it_kept_times_falls_back_on_the_archives_own(tmp_path):
    entries = {"data/processed/results/showcase.json": b"{}", "data/processed/results/risk.json": b"[]"}
    when = (2026, 9, 19, 12, 3, 30)
    z_path = tmp_path / "old.zip"
    with zipfile.ZipFile(z_path, "w") as z:
        for name, body in entries.items():
            z.writestr(zipfile.ZipInfo(name, date_time=when), body)
        z.writestr(MANIFEST, _manifest(entries))         # no "mtimes": an older bundle
    unpack_bundle(z_path, data_dir=tmp_path / "d")
    expected = time.mktime(when + (0, 0, -1))
    assert abs((tmp_path / "d" / "processed" / "results" / "risk.json").stat().st_mtime - expected) < 2


@pytest.mark.parametrize("bad", ["yesterday", 1e300, -5, True, None])
def test_a_time_that_makes_no_sense_is_not_believed(tmp_path, bad):
    body = b"{}"
    name = "data/processed/results/showcase.json"
    when = (2026, 10, 1, 8, 0, 0)
    z_path = tmp_path / "x.zip"
    with zipfile.ZipFile(z_path, "w") as z:
        z.writestr(zipfile.ZipInfo(name, date_time=when), body)
        z.writestr(MANIFEST, json.dumps({"kapacitet": "x", "created": "", "level": "full", "web": False,
                                         "files": {name: hashlib.sha256(body).hexdigest()},
                                         "mtimes": {name: bad}}))
    unpack_bundle(z_path, data_dir=tmp_path / "d")
    got = (tmp_path / "d" / "processed" / "results" / "showcase.json").stat().st_mtime
    assert abs(got - time.mktime(when + (0, 0, -1))) < 2
