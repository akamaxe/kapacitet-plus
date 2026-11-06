"""Every new seal is signed with the installation's Ed25519 key (kapacitet/seals.py), so a rewrite
of the chain shows to anyone who holds the public key, even when the hashes were rebuilt. No data
needed: the conftest fixtures point the chain at the test's own folder and the key at the
session's; the tests that rotate the key give it a folder of their own."""

from __future__ import annotations

import base64
import json
import logging
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _chain(seals):
    return seals.folder() / "seals.jsonl"


def _read(seals) -> list[dict]:
    return [json.loads(line) for line in _chain(seals).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(seals, lines: list[dict]) -> None:
    _chain(seals).write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in lines), encoding="utf-8")


def _rehash(seals, lines: list[dict]) -> list[dict]:
    """What anyone who can write the folder can do: rebuild every chain hash after an edit."""
    prev = seals.GENESIS
    for i, e in enumerate(lines, start=1):
        e["seq"], e["prev"] = i, prev
        e["chain"] = seals._chain_hash(prev, e)
        prev = e["chain"]
    return lines


def _legacy_line(seals, seq: int, prev: str, item: str) -> dict:
    """A line as the code before signing wrote it."""
    e = {"seq": seq, "at": f"2026-10-0{seq}T10:00:00+00:00", "kind": "decision", "id": item,
         "digest": seals.digest({"i": seq}), "fingerprint": "f" * 64, "prev": prev}
    e["chain"] = seals._chain_hash(prev, e)
    return e


def _secret_forms(key: Ed25519PrivateKey) -> list[bytes]:
    """The private key in every form it could leak in: its PEM, its raw bytes and their base64/hex."""
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    body = b"".join(pem.splitlines()[1:-1])
    return [raw, base64.b64encode(raw), raw.hex().encode(), body[16:48]]


def test_new_lines_are_signed_and_the_public_key_is_published_next_to_the_chain():
    from kapacitet import seals

    for i in range(3):
        seals.seal("decision", f"a{i:011d}", {"decision": "firm", "i": i})
    out = seals.verify_chain()
    info = seals.public_key_info()
    assert out["ok"] and out["signed"] == 3 and out["legacy"] == 0 and out["warnings"] == []
    assert out["keys"] == [info["key_id"]] and out["trust"] == "installation"
    lines = _read(seals)
    assert all(e["key_id"] == info["key_id"] and e["signed_since"] == 1 for e in lines)
    # key_id is the sha256 of the raw public key, and the signature covers the line without sig
    pub = seals.load_public((seals.folder() / seals.PUBLISHED).read_bytes())
    assert seals.public_pem(pub) == info["public_key"] and seals.key_id_of(pub) == info["key_id"]
    line = lines[1]
    message = seals.digest({k: v for k, v in line.items() if k != "sig"}).encode()
    pub.verify(base64.b64decode(line["sig"]), message)
    assert set(info["how_to_verify"]) == {"en", "sv"}
    v = seals.verify("decision", "a00000000001")
    assert v["ok"] and v["signed"] and v["key_id"] == info["key_id"] and v["chain_reason"] is None


def test_a_changed_line_or_signature_is_detected_even_with_the_hashes_rebuilt():
    from kapacitet import seals

    for i in range(3):
        seals.seal("decision", f"b{i:011d}", {"decision": "firm", "i": i})
    good = _read(seals)

    # the copy and the line's digest changed, and every hash after it rebuilt: only the signature knows
    lines = json.loads(json.dumps(good))
    lines[1]["digest"] = seals.digest({"decision": "declined", "i": 1})
    _write(seals, _rehash(seals, lines))
    out = seals.verify_chain()
    assert not out["ok"] and out["broken_at"] == 2 and out["reason"] == "signature"
    assert not seals.verify("decision", "b00000000002")["ok"]

    # a field outside the hash chain (when signing began) is still under the signature
    lines = json.loads(json.dumps(good))
    lines[2]["signed_since"] = 3
    _write(seals, lines)
    assert seals.verify_chain()["reason"] == "signature"

    # a signature changed by one bit
    lines = json.loads(json.dumps(good))
    sig = bytearray(base64.b64decode(lines[0]["sig"]))
    sig[0] ^= 1
    lines[0]["sig"] = base64.b64encode(bytes(sig)).decode()
    _write(seals, lines)
    assert seals.verify_chain() == {**seals.verify_chain(), "ok": False, "broken_at": 1, "reason": "signature"}

    # re-signed with a key of the forger's own: not a key anyone trusts
    forger = Ed25519PrivateKey.generate()
    lines = json.loads(json.dumps(good))
    lines[1]["key_id"] = seals.key_id_of(forger.public_key())
    lines[1]["sig"] = base64.b64encode(forger.sign(seals._line_message(lines[1]))).decode()
    _write(seals, lines)
    assert seals.verify_chain()["reason"] == "unknown_key"

    # the signature stripped off the last line, or off every line from the second on
    lines = json.loads(json.dumps(good))
    for k in ("sig", "key_id", "signed_since"):
        lines[2].pop(k)
    _write(seals, lines)
    assert seals.verify_chain()["reason"] == "unsigned" and seals.verify_chain()["broken_at"] == 3
    _write(seals, good)
    assert seals.verify_chain()["ok"]


def test_a_chain_with_legacy_unsigned_lines_verifies_with_a_warning():
    from kapacitet import seals

    first = _legacy_line(seals, 1, seals.GENESIS, "c00000000001")
    second = _legacy_line(seals, 2, first["chain"], "c00000000002")
    seals.folder().mkdir(parents=True, exist_ok=True)
    _write(seals, [first, second])
    assert seals.verify_chain()["ok"] and seals.verify_chain()["legacy"] == 2
    entry = seals.seal("decision", "c00000000003", {"decision": "firm"})
    assert entry["signed_since"] == 3 and entry["prev"] == second["chain"]
    out = seals.verify_chain()
    assert out["ok"] and out["legacy"] == 2 and out["signed"] == 1 and out["warnings"] == ["legacy_unsigned"]
    assert seals.verify_chain(require_signed=True)["reason"] == "unsigned"
    # a legacy line is still held by the hash chain, and the signed line after it holds that hash
    lines = _read(seals)
    lines[0]["digest"] = "0" * 64
    _write(seals, _rehash(seals, lines))
    assert seals.verify_chain()["reason"] == "signature" and seals.verify_chain()["broken_at"] == 3
    # the first signed line cannot pass for a legacy one: the next line says where signing began
    _write(seals, [first, second])
    seals.seal("decision", "c00000000005", {"decision": "firm", "n": 5})
    seals.seal("decision", "c00000000006", {"decision": "firm", "n": 6})
    lines = _read(seals)
    for k in ("sig", "key_id", "signed_since"):
        lines[2].pop(k)
    _write(seals, lines)
    out = seals.verify_chain()
    assert not out["ok"] and out["reason"] == "unsigned" and out["broken_at"] == 3


def test_the_command_line_verifies_a_chain_with_the_published_key(tmp_path, capsys):
    from kapacitet import pipeline, seals

    first = _legacy_line(seals, 1, seals.GENESIS, "d00000000001")
    seals.folder().mkdir(parents=True, exist_ok=True)
    _write(seals, [first])
    seals.seal("decision", "d00000000002", {"decision": "firm"})
    published = seals.folder() / seals.PUBLISHED
    pipeline.main(["seals", "verify", str(seals.folder()), "--key", str(published)])
    said = capsys.readouterr().out
    assert "valid" in said and "1 legacy" in said and "1 signed" in said and "NOT VALID" not in said

    other = tmp_path / "other.pub"
    other.write_text(seals.public_pem(Ed25519PrivateKey.generate().public_key()), encoding="ascii")
    with pytest.raises(SystemExit) as stop:
        pipeline.main(["seals", "verify", str(_chain(seals)), "--key", str(other)])
    assert stop.value.code == 1 and "not trusted" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        pipeline.main(["seals", "verify", str(seals.folder()), "--require-signed"])
    assert stop.value.code == 1


@pytest.fixture()
def own_key(tmp_path, monkeypatch):
    """A key folder of the test's own, so rotating it leaves the session's key alone."""
    from kapacitet import seals

    folder = tmp_path / "keys"
    monkeypatch.setattr(seals, "key_dir", lambda: folder)
    return folder


def test_a_rotation_is_signed_by_both_keys_and_the_chain_verifies_across_it(own_key, tmp_path):
    from kapacitet import pipeline, seals

    seals.seal("decision", "e00000000001", {"decision": "firm"})
    old = seals.public_key_info()
    elsewhere = tmp_path / "study-evidence"
    with seals.chain_at(elsewhere):
        seals.seal("publication", "2026Q3.1", {"outputs": {}})
    pipeline.main(["seals", "rotate-key"])
    new = seals.public_key_info()
    assert new["key_id"] != old["key_id"]
    rec = json.loads((own_key / "rotations" / f"{new['key_id']}.json").read_text(encoding="utf-8"))
    assert seals.rotation_valid(rec)[:2] == (old["key_id"], new["key_id"])
    assert (own_key / "retired" / f"{old['key_id']}.pub").is_file()
    assert old["key_id"] not in (own_key / seals.KEY_FILE).read_text(encoding="ascii")
    seals.seal("decision", "e00000000002", {"decision": "firm"})

    lines = _read(seals)
    assert [e["kind"] for e in lines] == ["decision", "key_rotation", "decision"]
    assert [e["key_id"] for e in lines] == [old["key_id"], new["key_id"], new["key_id"]]
    keys = {k: seals.load_public(i["public_key"]) for k, i in (("old", old), ("new", new))}
    for trusted in ("old", "new"):          # whoever trusts either key trusts both
        out = seals.verify_chain({seals.key_id_of(keys[trusted]): keys[trusted]})
        assert out["ok"] and out["keys"] == sorted([old["key_id"], new["key_id"]])
    assert seals.verify_chain()["ok"]
    assert seals.public_key_info(old["key_id"])["public_key"] == old["public_key"]   # retired, still known

    # another chain gets the rotation on its next seal, so the old key's trust carries over there too
    with seals.chain_at(elsewhere):
        seals.seal("publication", "2026Q3.2", {"outputs": {"a": 1}})
        assert [e["kind"] for e in seals._lines()] == ["publication", "key_rotation", "publication"]
        assert seals.verify_chain({old["key_id"]: keys["old"]})["ok"]
        assert seals.load_public((elsewhere / seals.PUBLISHED).read_bytes()) is not None

    # a rotation record that one key did not sign extends no trust
    copy = seals.folder() / "sealed" / "key_rotation" / f"{new['key_id'][:16]}.json"
    forged = json.loads(copy.read_text(encoding="utf-8"))
    forged["sig_new"] = forged["sig_old"]
    assert seals.rotation_valid(forged) is None
    copy.write_text(json.dumps(forged), encoding="utf-8")
    out = seals.verify_chain({old["key_id"]: keys["old"]})
    assert not out["ok"] and out["reason"] == "unknown_key" and out["broken_at"] == 2
    with pytest.raises(ValueError):
        seals.erase("key_rotation", new["key_id"][:16], "a rotation stays")


def test_rotating_without_a_key_says_so(own_key, capsys):
    from kapacitet import pipeline

    with pytest.raises(SystemExit) as stop:
        pipeline.main(["seals", "rotate-key"])
    assert stop.value.code == 2 and "no signing key" in capsys.readouterr().out


def test_the_private_key_never_reaches_a_bundle_a_manifest_a_record_or_a_log(own_key, tmp_path, monkeypatch, caplog):
    from kapacitet import bundle, seals

    caplog.set_level(logging.DEBUG)
    data = tmp_path / "data"
    for rel, body in {"processed/results/showcase.json": b"{}", "raw/manifest.json": b"{}"}.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(body)
    # the key where KAPACITET_HOME/keys is by default, inside the data folder
    monkeypatch.setattr(seals, "key_dir", lambda: data / "keys")
    seals.seal("assessment", "0123456789ab", {"id": "0123456789ab"})
    seals.erase("assessment", "0123456789ab", "retention")
    first = seals.signing_key(create=False)
    seals.rotate_key()
    seals.seal("decision", "f00000000001", {"decision": "firm"})
    (data / "processed" / "results" / "stray.pem").write_bytes(b"-----BEGIN PRIVATE KEY-----")
    forms = _secret_forms(first) + _secret_forms(seals.signing_key(create=False))

    archive = tmp_path / "b.zip"
    manifest = bundle.make_bundle(archive, level="full", data_dir=data)
    assert not any("keys/" in name or name.endswith(".pem") for name in manifest["files"])
    with zipfile.ZipFile(archive) as z:
        blobs = [z.read(n) for n in z.namelist()]
    eipack_like = json.dumps(seals.public_key_info()).encode()
    outside = [p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and (data / "keys") not in p.parents]
    for blob in blobs + outside + [eipack_like, caplog.text.encode()]:
        assert not any(f in blob for f in forms)
    assert (data / "keys" / seals.KEY_FILE).is_file()


def test_the_api_publishes_the_key_and_the_ei_pack_carries_it():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kapacitet import seals
    from kapacitet.api import evidence, operation

    app = FastAPI()
    app.include_router(evidence.router)
    client = TestClient(app)
    seals.seal("decision", "g00000000001", {"decision": "firm"})
    key = client.get("/api/v1/seals/key").json()
    assert key["key_id"] == seals.public_key_info()["key_id"] and key["public_key"].startswith("-----BEGIN PUBLIC KEY")
    assert "PRIVATE" not in json.dumps(key)
    head = client.get("/api/v1/seals").json()
    assert head["ok"] and head["signed"] == 1 and head["key"]["key_id"] == key["key_id"]
    assert head["last"][0]["sig"]

    entry = seals.seal("eipack", "dayahead-maxmin-test", {"conditions": []})
    resp = operation._sealed_pack({"conditions": []}, entry, "dayahead", "maxmin")
    body = json.loads(resp.body)
    assert resp.headers["X-Seal-Key-Id"] == entry["key_id"]
    assert body["seal"]["key"]["public_key"] == key["public_key"] and body["seal"]["line"]["sig"] == entry["sig"]
    assert set(body["seal"]["key"]["how_to_verify"]) == {"en", "sv"}
    # what the sentence says is enough to check it: the pack's digest, then the line's signature
    pack = {k: v for k, v in body.items() if k != "seal"}
    assert seals.digest(pack) == body["seal"]["line"]["digest"]
    pub = seals.load_public(body["seal"]["key"]["public_key"])
    line = body["seal"]["line"]
    pub.verify(base64.b64decode(line["sig"]), seals.digest({k: v for k, v in line.items() if k != "sig"}).encode())
