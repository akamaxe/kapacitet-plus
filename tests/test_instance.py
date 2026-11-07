"""One installation per DSO (kapacitet/instance.py): the instance check finds each broken piece,
and a backup restores into an empty instance whole, on the files backend and on PostgreSQL."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kapacitet import instance, seals
from kapacitet.instance import Instance, InstanceError

from conftest import another_server

# the files backend's own folders are what most of these tests look at; the PostgreSQL ones point
# the store at the test database themselves
pytestmark = pytest.mark.files_only

VARS = ("KAPACITET_ALLOWED_HOSTS", "KAPACITET_CORS_ORIGINS", "KAPACITET_TSA_URL", "KAPACITET_TSA_CERT",
        "KAPACITET_TSA_TIMEOUT_S", "KAPACITET_TELEMETRY", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME",
        "KAPACITET_SETUP_UPLOAD", "KAPACITET_OIDC_ISSUER", "KAPACITET_OIDC_CLIENT_ID",
        "KAPACITET_OIDC_CLIENT_SECRET", "KAPACITET_SESSION_SECRET", "KAPACITET_OIDC_REDIRECT_URI")


def _make(root: Path, monkeypatch, results: bool = True) -> Instance:
    """An instance in `root`: its data folder (with stand-in results), requests and key folders."""
    inst = Instance(data=root / "data", requests=root / "data" / "requests", keys=root / "keys")
    inst.data.mkdir(parents=True)
    if results:
        for rel in ("grid/net.json", "results/showcase.json", "results/operation/index.json"):
            path = inst.data / "processed" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
    return inst


def _use(inst: Instance, monkeypatch) -> None:
    """Seal into this instance's chain with its key, as its server would."""
    monkeypatch.setattr(seals, "folder", lambda: inst.requests)
    monkeypatch.setattr(seals, "key_dir", lambda: inst.keys)


@pytest.fixture
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAPACITET_AUTH", "off")
    from kapacitet import auth

    monkeypatch.setattr(auth, "_dotenv", lambda: None)     # never a developer's .env


@pytest.fixture
def healthy(tmp_path, monkeypatch, clean_env):
    inst = _make(tmp_path / "a", monkeypatch)
    _use(inst, monkeypatch)
    for i in range(3):
        seals.seal("decision", f"d{i:011d}", {"decision": "firm", "i": i})
    return inst


def _levels(found, area):
    return [f.level for f in found if f.area == area]


def _errors(found):
    return [(f.area, f.message) for f in found if f.level == "error"]


# ---------------------------------------------------------------- check

def test_a_healthy_instance_passes_and_names_its_key(healthy):
    found = instance.check(healthy)
    assert _errors(found) == []
    key = [f.message for f in found if f.area == "seal key" and f.level == "ok"]
    assert key and seals.key_id_of(seals.signing_key().public_key())[:16] in key[0]
    assert any(f.area == "seal chain" and "3 lines, 3 signed, valid" in f.message for f in found)
    for area in ("data", "results", "database", "sign-in", "timestamps", "telemetry", "disk"):
        assert "ok" in _levels(found, area), area


def test_sign_in_on_is_reported_by_its_provider_host_and_no_secret_is_printed(healthy, monkeypatch):
    monkeypatch.setenv("KAPACITET_AUTH", "oidc")
    monkeypatch.setenv("KAPACITET_OIDC_ISSUER", "https://login.example.org/tenant-0000/v2.0")
    monkeypatch.setenv("KAPACITET_OIDC_CLIENT_ID", "client-id-1")
    monkeypatch.setenv("KAPACITET_OIDC_CLIENT_SECRET", "client-secret-value-xyz")
    monkeypatch.setenv("KAPACITET_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("KAPACITET_ALLOWED_HOSTS", "kapacitet.dso.example")
    monkeypatch.setenv("KAPACITET_DATABASE_URL", "postgresql://kapacitet:db-password-123@127.0.0.1:1/kapacitet")
    found = instance.check(healthy)
    text = instance.report(found) + instance.findings_json(found)
    assert "ok" in _levels(found, "sign-in") and "login.example.org" in text
    assert "ok" in _levels(found, "hosts")
    for secret in ("client-secret-value-xyz", "s" * 40, "db-password-123"):
        assert secret not in text


@pytest.mark.parametrize("piece", ["no_data_folder", "no_results", "auth_unset", "auth_incomplete", "hosts_star",
                                   "public_without_signin", "key_unreadable", "key_missing", "chain_broken",
                                   "tsa_url", "tsa_cert", "telemetry_endpoint", "disk_full", "db_unreachable",
                                   "db_pending"])
def test_each_broken_piece_is_an_error_of_its_own(healthy, monkeypatch, piece):
    inst = healthy
    area = {"no_data_folder": "data", "no_results": "results", "auth_unset": "sign-in", "auth_incomplete": "sign-in",
            "hosts_star": "hosts", "public_without_signin": "hosts", "key_unreadable": "seal key",
            "key_missing": "seal key", "chain_broken": "seal chain", "tsa_url": "timestamps",
            "tsa_cert": "timestamps", "telemetry_endpoint": "telemetry", "disk_full": "disk",
            "db_unreachable": "database", "db_pending": "database"}[piece]
    if piece == "no_data_folder":
        inst = Instance(data=inst.data.parent / "nowhere", requests=inst.requests, keys=inst.keys)
    elif piece == "no_results":
        (inst.data / "processed" / "results" / "showcase.json").unlink()
    elif piece == "auth_unset":
        monkeypatch.delenv("KAPACITET_AUTH")
    elif piece == "auth_incomplete":
        monkeypatch.setenv("KAPACITET_AUTH", "oidc")
    elif piece == "hosts_star":
        monkeypatch.setenv("KAPACITET_ALLOWED_HOSTS", "*")
    elif piece == "public_without_signin":
        monkeypatch.setenv("KAPACITET_ALLOWED_HOSTS", "kapacitet.dso.example")
    elif piece == "key_unreadable":
        (inst.keys / seals.KEY_FILE).write_text("not a key", encoding="ascii")
    elif piece == "key_missing":
        (inst.keys / seals.KEY_FILE).unlink()
    elif piece == "chain_broken":
        path = inst.requests / "seals.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"decision"', '"assessment"', 1)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif piece == "tsa_url":
        monkeypatch.setenv("KAPACITET_TSA_URL", "ftp://tsa.example")
    elif piece == "tsa_cert":
        monkeypatch.setenv("KAPACITET_TSA_URL", "https://freetsa.org/tsr")
        (inst.data / "tsa.pem").write_text("not a certificate", encoding="ascii")
        monkeypatch.setenv("KAPACITET_TSA_CERT", str(inst.data / "tsa.pem"))
    elif piece == "telemetry_endpoint":
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "collector-without-scheme")
    elif piece == "disk_full":
        monkeypatch.setattr(instance.shutil, "disk_usage",
                            lambda p: shutil._ntuple_diskusage(10 << 30, (10 << 30) - (100 << 20), 100 << 20))
    elif piece == "db_unreachable":
        monkeypatch.setenv("KAPACITET_DATABASE_URL", "postgresql://kapacitet@127.0.0.1:1/kapacitet")
    elif piece == "db_pending":
        pytest.importorskip("psycopg")
        from kapacitet.store import migrate

        monkeypatch.setenv("KAPACITET_DATABASE_URL", "postgresql://kapacitet@127.0.0.1:1/kapacitet")
        monkeypatch.setattr(migrate, "status", lambda url=None: {
            "migrations": [{"version": 1, "name": "x", "applied": True, "applied_at": "", "changed": False},
                           {"version": 2, "name": "y", "applied": False, "applied_at": None, "changed": False}],
            "pending": [2], "changed": [], "unknown": []})
    found = instance.check(inst)
    errors = _errors(found)
    assert [a for a, _ in errors] == [area], errors
    if piece == "db_pending":
        assert "kapacitet db migrate" in errors[0][1]
    if piece == "key_missing":
        assert "restore the key" in errors[0][1]


def test_the_command_exits_1_on_an_error_and_0_otherwise(healthy, monkeypatch, capsys):
    from kapacitet.pipeline import main

    monkeypatch.setattr(Instance, "current", classmethod(lambda cls: healthy))
    with pytest.raises(SystemExit) as done:
        main(["instance", "check"])
    assert done.value.code == 0 and "0 errors" in capsys.readouterr().out
    monkeypatch.setenv("KAPACITET_ALLOWED_HOSTS", "*")
    with pytest.raises(SystemExit) as done:
        main(["instance", "check", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert done.value.code == 1 and out["ok"] is False


# ---------------------------------------------------------------- backup and restore, files

def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _fill(inst: Instance) -> None:
    """State of every kind the files backend keeps, and a cache that is not state."""
    (inst.requests / "queue").mkdir(parents=True, exist_ok=True)
    (inst.requests / "queue" / "queue.json").write_text('{"version": 3}', encoding="utf-8")
    (inst.requests / "0123456789ab.json.gz").write_bytes(b"\x1f\x8b not really")
    (inst.requests / "index.jsonl").write_text('{"id": "0123456789ab"}\n', encoding="utf-8")
    (inst.requests / "settings.json").write_text('{"policy": "maxmin"}', encoding="utf-8")
    (inst.requests / "cache").mkdir(exist_ok=True)
    (inst.requests / "cache" / "pv_2025.npz").write_bytes(b"cache")


def test_backup_then_restore_round_trips_the_files_backend(healthy, tmp_path, monkeypatch):
    _fill(healthy)
    dest = tmp_path / "backup"
    manifest = instance.backup(dest, healthy)
    assert manifest["backend"] == "files" and manifest["seal_key"]["included"]
    assert not any(k.startswith("requests/cache") for k in manifest["files"])
    secret = dest / instance.SECRET_DIR
    assert "SECRET" in (secret / instance.KEY_README).read_text(encoding="utf-8")
    assert json.loads((secret / instance.KEY_MANIFEST).read_text(encoding="utf-8"))["secret"] is True

    target = _make(tmp_path / "b", monkeypatch)
    out = instance.restore(dest, target)
    assert out["chain"] == {"length": 3, "signed": 3, "ok": True} and not out["warnings"]
    want = {k: v for k, v in _tree(healthy.requests).items() if not k.startswith("cache/")}
    assert _tree(target.requests) == want
    assert _tree(target.keys) == _tree(healthy.keys)
    assert out["seal_key"] == manifest["seal_key"]["key_id"]

    # the restored instance seals on with the same key and its chain still verifies
    _use(target, monkeypatch)
    seals.seal("decision", "d00000000009", {"decision": "firm"})
    got = seals.verify_chain()
    assert got["ok"] and got["length"] == 4 and got["keys"] == [manifest["seal_key"]["key_id"]]
    assert _errors(instance.check(target)) == []

    with pytest.raises(InstanceError, match="already holds"):
        instance.restore(dest, target)


def test_a_damaged_backup_writes_nothing(healthy, tmp_path, monkeypatch):
    dest = tmp_path / "backup"
    instance.backup(dest, healthy)
    path = dest / "requests" / "seals.jsonl"
    path.write_bytes(path.read_bytes() + b" ")
    target = _make(tmp_path / "b", monkeypatch)
    with pytest.raises(InstanceError, match="damaged"):
        instance.restore(dest, target)
    assert not target.requests.exists() and not target.keys.exists()


def test_the_key_is_never_restored_over_another_nor_left_out_by_accident(healthy, tmp_path, monkeypatch):
    dest, elsewhere = tmp_path / "backup", tmp_path / "vault"
    manifest = instance.backup(dest, healthy, key_out=elsewhere)
    assert not (dest / instance.SECRET_DIR).exists() and (elsewhere / seals.KEY_FILE).is_file()
    target = _make(tmp_path / "b", monkeypatch)
    with pytest.raises(InstanceError, match="--key-from"):
        instance.restore(dest, target)
    target.keys.mkdir(parents=True)
    (target.keys / seals.KEY_FILE).write_bytes(b"another key")
    with pytest.raises(InstanceError, match="already holds a signing key"):
        instance.restore(dest, target, key_from=elsewhere)
    assert not target.requests.exists()
    out = instance.restore(dest, target, include_key=False)
    assert out["seal_key"] is None and out["files"] == len(manifest["files"])


def test_a_backup_goes_into_an_empty_folder_and_never_beside_a_running_server(healthy, tmp_path):
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    with pytest.raises(InstanceError, match="not empty"):
        instance.backup(full, healthy)
    with another_server(healthy.data):
        with pytest.raises(InstanceError, match="stop it"):
            instance.backup(tmp_path / "backup", healthy)
    assert not (tmp_path / "backup").exists()          # not even the key was written


# ---------------------------------------------------------------- backup and restore, PostgreSQL

def _client_too_old(url: str) -> bool:
    """pg_dump refuses a server of a newer major version than its own."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    return (instance.pg_major(instance.pg_tool("pg_dump")) or 0) < server


def test_check_passes_on_a_migrated_test_database(healthy, monkeypatch):
    from conftest import fresh_test_schema, use_database

    scope = use_database(monkeypatch, fresh_test_schema())
    found = instance.check(healthy)
    assert any(f.area == "database" and f.level == "ok" and scope in f.message for f in found)
    assert [a for a, _ in _errors(found)] == []


def test_backup_then_restore_round_trips_the_postgres_backend(tmp_path, monkeypatch, clean_env):
    from conftest import fresh_test_schema, use_database

    url = fresh_test_schema()
    if instance.pg_tool("pg_dump") is None or instance.pg_tool("pg_restore") is None:
        pytest.skip("pg_dump and pg_restore are not on the PATH (KAPACITET_PG_BIN)")
    if _client_too_old(url):
        pytest.skip("pg_dump is older than the test database's server")
    inst = _make(tmp_path / "a", monkeypatch)
    _use(inst, monkeypatch)
    scope = use_database(monkeypatch, url)
    from kapacitet import store
    from kapacitet.store import db

    for i in range(3):
        seals.seal("decision", f"d{i:011d}", {"decision": "firm", "i": i})
    before = seals.verify_chain()
    dest = tmp_path / "backup"
    manifest = instance.backup(dest, inst)
    assert manifest["backend"] == "postgres" and manifest["scope"] == scope and instance.DUMP in manifest["files"]

    import psycopg

    db.close_all()
    with psycopg.connect(url, autocommit=True) as conn:
        assert "test" in conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    target = _make(tmp_path / "b", monkeypatch)
    _use(target, monkeypatch)
    out = instance.restore(dest, target)
    assert out["chain"]["ok"] and out["chain"]["length"] == before["length"] and not out["warnings"]
    assert store.scope() == scope and seals.verify_chain()["head"] == before["head"]
    assert _tree(target.keys) == _tree(inst.keys)
