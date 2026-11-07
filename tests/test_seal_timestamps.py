"""RFC 3161 timestamps on the seal chain (kapacitet/tsa.py, kapacitet/seals.py), against a fake TSA
in this process: a self-signed TSA certificate made with cryptography, and tokens built with
pyasn1-modules' RFC 3161 and CMS structures (so the reading side, rfc3161ng, is checked against an
independent writer). No test reaches a real TSA: conftest turns KAPACITET_TSA_URL off and these
tests send their requests through an httpx transport of their own.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("rfc3161ng", reason="the timestamp extra: pip install -e \".[timestamp]\"")

import httpx  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402
from pyasn1.codec.der import decoder, encoder  # noqa: E402
from pyasn1.type import univ, useful  # noqa: E402
from pyasn1_modules import rfc3161, rfc5280, rfc5652  # noqa: E402

from conftest import files_only  # noqa: E402

TSA_URL = "https://tsa.invalid/tsr"
GEN_TIME = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
SHA256 = univ.ObjectIdentifier("2.16.840.1.101.3.4.2.1")
RSA_ENCRYPTION = univ.ObjectIdentifier("1.2.840.113549.1.1.1")


def _cert(name: str, stamping: bool = True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(GEN_TIME - timedelta(days=30)).not_valid_after(GEN_TIME + timedelta(days=365)))
    if stamping:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return key, builder.sign(key, hashes.SHA256())


def _attribute(oid, value) -> rfc5652.Attribute:
    attr = rfc5652.Attribute()
    attr["attrType"] = oid
    attr["attrValues"].setComponentByPosition(0, univ.Any(encoder.encode(value)))
    return attr


def make_token(key, cert, imprint: bytes, nonce: int | None, serial: int = 1, when: datetime = GEN_TIME) -> bytes:
    """A TimeStampToken (DER) over a SHA-256 `imprint`, as a TSA signs it: a CMS SignedData whose
    content is the TSTInfo, signed over its signed attributes (content type and message digest)."""
    info = rfc3161.TSTInfo()
    info["version"] = 1
    info["policy"] = univ.ObjectIdentifier("1.3.6.1.4.1.99999.1")
    info["messageImprint"]["hashAlgorithm"]["algorithm"] = SHA256
    info["messageImprint"]["hashedMessage"] = imprint
    info["serialNumber"] = serial
    info["genTime"] = useful.GeneralizedTime(when.strftime("%Y%m%d%H%M%SZ"))
    if nonce is not None:
        info["nonce"] = nonce
    content = encoder.encode(info)

    attrs = [_attribute(rfc5652.id_contentType, rfc3161.id_ct_TSTInfo),
             _attribute(rfc5652.id_messageDigest, univ.OctetString(hashlib.sha256(content).digest()))]
    to_sign = univ.SetOf(componentType=rfc5652.Attribute())
    for i, a in enumerate(attrs):
        to_sign.setComponentByPosition(i, a)
    signature = key.sign(encoder.encode(to_sign), padding.PKCS1v15(), hashes.SHA256())

    cert_asn1, _ = decoder.decode(cert.public_bytes(serialization.Encoding.DER), asn1Spec=rfc5280.Certificate())
    signer = rfc5652.SignerInfo()
    signer["version"] = 1
    signer["sid"]["issuerAndSerialNumber"]["issuer"] = cert_asn1["tbsCertificate"]["issuer"]
    signer["sid"]["issuerAndSerialNumber"]["serialNumber"] = cert_asn1["tbsCertificate"]["serialNumber"]
    signer["digestAlgorithm"]["algorithm"] = SHA256
    for i, a in enumerate(attrs):
        signer["signedAttrs"].setComponentByPosition(i, a)
    signer["signatureAlgorithm"]["algorithm"] = RSA_ENCRYPTION
    signer["signature"] = signature

    signed = rfc5652.SignedData()
    signed["version"] = 3
    alg = rfc5280.AlgorithmIdentifier()
    alg["algorithm"] = SHA256
    signed["digestAlgorithms"].setComponentByPosition(0, alg)
    signed["encapContentInfo"]["eContentType"] = rfc3161.id_ct_TSTInfo
    signed["encapContentInfo"]["eContent"] = content
    signed["certificates"].setComponentByPosition(0)
    signed["certificates"][0]["certificate"] = cert_asn1
    signed["signerInfos"].setComponentByPosition(0, signer)

    wrapper = rfc5652.ContentInfo()
    wrapper["contentType"] = rfc5652.id_signedData
    wrapper["content"] = encoder.encode(signed)
    return encoder.encode(wrapper)


class FakeTSA:
    """An RFC 3161 TSA behind an httpx transport: it answers each request with a token over the
    request's hash and nonce, or fails like a TSA that cannot be reached."""

    def __init__(self, down: bool = False):
        self.key, self.cert = _cert("Fake TSA")
        self.down = down
        self.requests: list[bytes] = []
        self.serial = 0

    @property
    def cert_pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.content)
        if self.down:
            raise httpx.ConnectError("no route to the TSA", request=request)
        assert request.headers["content-type"] == "application/timestamp-query"
        req, rest = decoder.decode(request.content, asn1Spec=rfc3161.TimeStampReq())
        assert not rest
        nonce = req["nonce"]
        self.serial += 1
        token = make_token(self.key, self.cert, bytes(req["messageImprint"]["hashedMessage"]),
                           int(nonce) if nonce.isValue else None, serial=self.serial)
        resp = rfc3161.TimeStampResp()
        resp["status"]["status"] = 0
        resp["timeStampToken"], _ = decoder.decode(token, asn1Spec=rfc3161.TimeStampToken())
        return httpx.Response(200, content=encoder.encode(resp), headers={"Content-Type": "application/timestamp-reply"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.fixture()
def fake_tsa(monkeypatch):
    from kapacitet import tsa

    fake = FakeTSA()
    monkeypatch.setenv(tsa.ENV_URL, TSA_URL)
    monkeypatch.setattr(tsa, "TRANSPORT", fake.transport())
    return fake


def _sealed(seals):
    return seals.folder() / "sealed"


def test_no_tsa_set_means_no_request_and_no_marker():
    from kapacitet import seals

    seals.seal("decision", "d1", {"decision": "firm"})
    assert seals._chain().stamps() == {} and seals._chain().stamps_pending() == {}
    out = seals.verify_timestamps()
    assert out["ok"] and out["tokens"] == 0 and out["unstamped"] == 1


@files_only
def test_a_token_verifies_and_only_the_hash_leaves(fake_tsa):
    from kapacitet import seals, tsa

    entry = seals.seal("decision", "d1", {"decision": "firm", "address": "Gatan 1"})
    token = (_sealed(seals) / "1.tsr").read_bytes()
    assert not (_sealed(seals) / "1.tsa_pending").exists()

    # the request held the line's SHA-256 and nothing of the record
    (sent,) = fake_tsa.requests
    req, _ = decoder.decode(sent, asn1Spec=rfc3161.TimeStampReq())
    assert bytes(req["messageImprint"]["hashedMessage"]) == seals.line_digest(entry) == hashlib.sha256(seals.canonical(entry)).digest()
    assert b"Gatan" not in sent and b"firm" not in sent and len(sent) < 120

    assert tsa.verify(token, seals.line_digest(entry), fake_tsa.cert_pem) == GEN_TIME
    out = seals.verify_timestamps(fake_tsa.cert_pem)
    assert out["ok"] and out["checked"] and out["tsa"] == "Fake TSA"
    assert out["attested"] == [{"seq": 1, "time": GEN_TIME.isoformat(), "at": entry["at"]}]
    assert out["pending"] == [] and out["unstamped"] == 0
    assert seals.verify_chain()["ok"]

    # a chain file that lists its CA first still yields the TSA's certificate
    _, ca = _cert("Some CA", stamping=False)
    bundle = ca.public_bytes(serialization.Encoding.PEM) + fake_tsa.cert_pem
    assert tsa.describe_cert(tsa.load_cert(bundle)) == "Fake TSA"
    assert tsa.verify(token, seals.line_digest(entry), bundle) == GEN_TIME


def test_the_seal_does_not_wait_on_the_tsa(fake_tsa, monkeypatch):
    """The request runs on a thread of its own: the line is owed its token until the token is kept."""
    import threading

    from kapacitet import seals, tsa

    monkeypatch.setattr(seals, "STAMP_IN_BACKGROUND", True)
    gate, real = threading.Event(), fake_tsa.handle

    def slow(request):
        assert gate.wait(10)
        return real(request)

    monkeypatch.setattr(tsa, "TRANSPORT", httpx.MockTransport(slow))
    seals.seal("decision", "d1", {"decision": "firm"})       # returns while the TSA has not answered
    assert json.loads(seals._chain().stamps_pending()[1])["reason"] == "queued"
    gate.set()
    seals.wait_stamps(10)
    assert list(seals._chain().stamps()) == [1] and seals._chain().stamps_pending() == {}
    assert seals.verify_timestamps(fake_tsa.cert_pem)["ok"]


@files_only
def test_a_changed_digest_another_key_or_a_cut_chain_fails(fake_tsa):
    from kapacitet import seals, tsa

    first = seals.seal("decision", "d1", {"decision": "firm"})
    second = seals.seal("decision", "d2", {"decision": "nonfirm"})
    t1, t2 = (_sealed(seals) / "1.tsr").read_bytes(), (_sealed(seals) / "2.tsr").read_bytes()

    with pytest.raises(tsa.TimestampError) as exc:
        tsa.verify(t1, seals.line_digest(second), fake_tsa.cert_pem)
    assert exc.value.reason == "digest"
    _, other = _cert("Another TSA")
    with pytest.raises(tsa.TimestampError) as exc:
        tsa.verify(t1, seals.line_digest(first), other.public_bytes(serialization.Encoding.PEM))
    assert exc.value.reason == "signature"
    _, plain = _cert("Not a TSA", stamping=False)
    with pytest.raises(tsa.TimestampError) as exc:
        tsa.verify(t1, seals.line_digest(first), plain.public_bytes(serialization.Encoding.PEM))
    assert exc.value.reason == "certificate"

    # a token moved to another line no longer holds, even without the certificate
    (_sealed(seals) / "1.tsr").write_bytes(t2)
    out = seals.verify_timestamps()
    assert not out["ok"] and [(f["seq"], f["reason"]) for f in out["failed"]] == [(1, "digest")]
    (_sealed(seals) / "1.tsr").write_bytes(t1)
    assert seals.verify_timestamps(fake_tsa.cert_pem)["ok"]

    # cutting the last line off the chain leaves the rest valid, but its token shows it
    path = seals.folder() / "seals.jsonl"
    path.write_text(path.read_text(encoding="utf-8").splitlines(keepends=True)[0], encoding="utf-8")
    out = seals.verify_timestamps(fake_tsa.cert_pem)
    assert not out["ok"] and [(f["seq"], f["reason"]) for f in out["failed"]] == [(2, "missing_line")]


def test_an_unreachable_tsa_leaves_the_seal_written_and_pending(fake_tsa):
    from kapacitet import seals

    fake_tsa.down = True
    entry = seals.seal("decision", "d1", {"decision": "firm"})      # does not raise
    assert seals.entry_for("decision", "d1") == entry and seals.verify_chain()["ok"]
    owed = seals._chain().stamps_pending()
    assert list(owed) == [1] and seals._chain().stamps() == {}
    marker = json.loads(owed[1])
    assert marker["reason"] == "unreachable" and marker["line_digest"] == seals.line_digest(entry).hex()
    assert marker["tries"] == 1

    # the next seal does not wait on the TSA that just failed
    seals.seal("decision", "d2", {"decision": "nonfirm"})
    assert len(fake_tsa.requests) == 1
    assert json.loads(seals._chain().stamps_pending()[2])["reason"] == "backoff"
    out = seals.verify_timestamps()
    assert out["ok"] and out["pending"] == [1, 2] and out["tokens"] == 0

    # still down: the retry keeps them owed, and counts the tries
    assert seals.timestamp_pending() == {"stamped": [], "pending": [1, 2], "changed": [], "unknown": []}
    assert json.loads(seals._chain().stamps_pending()[1])["tries"] == 2

    fake_tsa.down = False
    assert seals.timestamp_pending()["stamped"] == [1, 2]
    assert seals._chain().stamps_pending() == {}
    out = seals.verify_timestamps(fake_tsa.cert_pem)
    assert out["ok"] and [a["seq"] for a in out["attested"]] == [1, 2]


def test_lines_sealed_before_a_tsa_was_set_are_stamped_with_missing(monkeypatch):
    from kapacitet import seals, tsa

    seals.seal("decision", "d1", {"decision": "firm"})
    fake = FakeTSA()
    monkeypatch.setenv(tsa.ENV_URL, TSA_URL)
    monkeypatch.setattr(tsa, "TRANSPORT", fake.transport())
    assert seals.timestamp_pending()["stamped"] == []                # nothing was owed
    assert seals.timestamp_pending(missing=True)["stamped"] == [1]
    assert seals.verify_timestamps(fake.cert_pem)["ok"]


def test_a_token_that_does_not_answer_the_request_is_refused(fake_tsa, monkeypatch):
    from kapacitet import seals, tsa

    real = fake_tsa.handle

    def answer(token_for):
        def handle(request):
            resp = real(request)
            req, _ = decoder.decode(request.content, asn1Spec=rfc3161.TimeStampReq())
            got, _ = decoder.decode(resp.content, asn1Spec=rfc3161.TimeStampResp())
            token = token_for(bytes(req["messageImprint"]["hashedMessage"]), int(req["nonce"]))
            got["timeStampToken"], _ = decoder.decode(token, asn1Spec=rfc3161.TimeStampToken())
            return httpx.Response(200, content=encoder.encode(got))
        return httpx.MockTransport(handle)

    # the right hash with another nonce: what a replayed answer looks like
    monkeypatch.setattr(tsa, "TRANSPORT", answer(lambda imprint, nonce: make_token(fake_tsa.key, fake_tsa.cert, imprint, nonce + 1)))
    with pytest.raises(tsa.TimestampError) as exc:
        tsa.request(hashlib.sha256(b"x").digest())
    assert exc.value.reason == "nonce"

    # the right nonce over another hash
    monkeypatch.setattr(tsa, "TRANSPORT", answer(lambda imprint, nonce: make_token(fake_tsa.key, fake_tsa.cert, bytes(32), nonce)))
    with pytest.raises(tsa.TimestampError) as exc:
        tsa.request(hashlib.sha256(b"x").digest())
    assert exc.value.reason == "digest"
    seals.seal("decision", "d1", {"decision": "firm"})
    assert json.loads(seals._chain().stamps_pending()[1])["reason"] == "digest"


@files_only
def test_the_command_line_fetches_and_verifies(fake_tsa, monkeypatch, tmp_path, capsys):
    from kapacitet import pipeline, seals, tsa

    fake_tsa.down = True
    seals.seal("decision", "d1", {"decision": "firm"})
    with pytest.raises(SystemExit) as exc:
        pipeline.main(["seals", "timestamp"])
    assert exc.value.code == 1 and "1 still owed" in capsys.readouterr().out

    fake_tsa.down = False
    pipeline.main(["seals", "timestamp"])
    assert "1 lines timestamped, 0 still owed" in capsys.readouterr().out

    pipeline.main(["seals", "verify"])
    out = capsys.readouterr().out
    assert "signatures unchecked" in out and out.rstrip().endswith("valid")

    cert = tmp_path / "tsa.pem"
    cert.write_bytes(fake_tsa.cert_pem)
    monkeypatch.setenv(tsa.ENV_CERT, str(cert))
    pipeline.main(["seals", "verify", "--times"])
    out = capsys.readouterr().out
    assert "1 of 1 tokens hold (attested by Fake TSA)" in out and f"attested {GEN_TIME.isoformat()}" in out

    (seals.folder() / "sealed" / "1.tsr").write_bytes(make_token(fake_tsa.key, fake_tsa.cert, bytes(32), None))
    with pytest.raises(SystemExit) as exc:
        pipeline.main(["seals", "verify"])
    assert exc.value.code == 1 and "NOT VALID: a timestamp does not hold" in capsys.readouterr().out

    monkeypatch.setenv(tsa.ENV_URL, "")
    with pytest.raises(SystemExit) as exc:
        pipeline.main(["seals", "timestamp"])
    assert exc.value.code == 2 and "KAPACITET_TSA_URL is not set" in capsys.readouterr().out
