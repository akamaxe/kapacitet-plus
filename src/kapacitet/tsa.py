"""RFC 3161 timestamps for the seal chain (kapacitet/seals.py).

The time in a chain line is the server's own clock, and a signature does not change that. With
KAPACITET_TSA_URL set, each new line is also sent to that time-stamping authority (TSA): the request
holds only the SHA-256 of the line's canonical JSON (its signature included), 32 bytes from which
nothing about the record, the house or the person can be read back. The TSA answers with a token it
signs over that hash and the time it names, so the line provably existed at that time, whoever
keeps the folder. FreeTSA (https://freetsa.org/tsr, free and without an account) is the documented
example; any RFC 3161 TSA works, and nothing here contacts one unless KAPACITET_TSA_URL is set.

The TSA's certificate is never fetched here. `kapacitet seals verify` checks the tokens against the
PEM file named by KAPACITET_TSA_CERT, which the owner downloads from the TSA once and keeps.

`request` asks for a token and checks that it answers the request (the hash, the nonce); `verify`
checks a kept token against the certificate and returns the attested time. rfc3161ng and pyasn1 are
the `timestamp` extra; without them a request fails as `missing`, and the seal it was for is left
pending like any other failure.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

import httpx

ENV_URL = "KAPACITET_TSA_URL"
ENV_CERT = "KAPACITET_TSA_CERT"
ENV_TIMEOUT = "KAPACITET_TSA_TIMEOUT_S"
EXAMPLE_URL = "https://freetsa.org/tsr"
TIMEOUT_S = 5.0
HASH = "sha256"
ID_KP_TIME_STAMPING = "1.3.6.1.5.5.7.3.8"
# an httpx transport to send the requests through instead of the network (the tests' fake TSA)
TRANSPORT: httpx.BaseTransport | None = None


class TimestampError(Exception):
    """A token that could not be had or does not hold. `reason` is one of: missing (rfc3161ng is
    not installed), unreachable, http, rejected, malformed, digest (the token is over another
    hash), nonce, certificate (not a TSA certificate, or not valid at the attested time) or
    signature."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _env(name: str) -> str:
    from .config import load_dotenv

    load_dotenv()                     # like the other settings: the environment, or .env
    return os.environ.get(name, "").strip()


def url() -> str:
    """The TSA in use, or "" when timestamps are off."""
    return _env(ENV_URL)


def cert_path() -> str:
    return _env(ENV_CERT)


def _timeout() -> float:
    try:
        return max(0.5, float(_env(ENV_TIMEOUT) or TIMEOUT_S))
    except ValueError:
        return TIMEOUT_S


def _lib():
    try:
        import rfc3161ng
        from pyasn1.codec.der import encoder
    except ImportError as exc:
        raise TimestampError("missing", "rfc3161ng is not installed (pip install -e \".[timestamp]\")") from exc
    return rfc3161ng, encoder


def digest_of(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def parse(token: bytes) -> dict:
    """What a token says, unchecked: {hash, imprint, time, nonce, serial}."""
    rfc, _ = _lib()
    from pyasn1.codec.der import decoder
    from pyasn1.error import PyAsn1Error

    try:
        tst, rest = decoder.decode(token, asn1Spec=rfc.TimeStampToken())
        if rest:
            raise ValueError("extra data after the token")
        info = tst.tst_info
        imprint = info["messageImprint"]
        nonce = info["nonce"]
        return {"hash": rfc.oid_to_hash.get(imprint["hashAlgorithm"]["algorithm"]),
                "imprint": bytes(imprint["hashedMessage"]),
                "time": rfc.get_timestamp(tst, naive=False).astimezone(timezone.utc),
                "nonce": int(nonce) if nonce.isValue else None,
                "serial": int(info["serialNumber"])}
    except (PyAsn1Error, ValueError, KeyError, TypeError, IndexError) as exc:
        raise TimestampError("malformed", f"not an RFC 3161 timestamp token ({type(exc).__name__})") from exc


def request(digest: bytes, tsa_url: str | None = None) -> bytes:
    """Ask the TSA for a token over `digest` (a SHA-256); returns the token (DER). Raises
    TimestampError when there is none or it does not answer this request."""
    rfc, encoder = _lib()
    target = tsa_url or url()
    if not target:
        raise TimestampError("unreachable", f"{ENV_URL} is not set")
    nonce = int.from_bytes(os.urandom(8), "big")
    body = rfc.encode_timestamp_request(rfc.make_timestamp_request(
        digest=digest, hashname=HASH, include_tsa_certificate=True, nonce=nonce))
    try:
        with httpx.Client(transport=TRANSPORT, timeout=_timeout(), follow_redirects=False) as client:
            resp = client.post(target, content=body, headers={"Content-Type": "application/timestamp-query",
                                                              "Accept": "application/timestamp-reply"})
    except httpx.HTTPError as exc:      # the address is left out: it may carry credentials
        raise TimestampError("unreachable", f"the TSA could not be reached ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise TimestampError("http", f"the TSA answered HTTP {resp.status_code}")
    try:
        tsr = rfc.decode_timestamp_response(resp.content)
        status = int(tsr["status"]["status"])
        token = tsr["timeStampToken"]
        present = token.isValue
    except Exception as exc:            # pyasn1 raises several kinds on a body that is not a reply
        raise TimestampError("malformed", f"the TSA's reply is not an RFC 3161 response ({type(exc).__name__})") from exc
    if status not in (0, 1) or not present:   # granted, grantedWithMods
        raise TimestampError("rejected", f"the TSA refused the request (status {status})")
    der = encoder.encode(token)
    got = parse(der)
    if got["hash"] != HASH or got["imprint"] != digest:
        raise TimestampError("digest", "the TSA's token is over another hash than the one sent")
    if got["nonce"] != nonce:
        raise TimestampError("nonce", "the TSA's token does not carry the request's nonce")
    return der


def load_cert(pem: bytes):
    """The first certificate of a PEM (or DER) file."""
    from cryptography import x509

    try:
        if b"-----BEGIN CERTIFICATE-----" in pem:
            return x509.load_pem_x509_certificates(pem)[0]
        return x509.load_der_x509_certificate(pem)
    except ValueError as exc:
        raise TimestampError("certificate", "not an X.509 certificate (PEM or DER)") from exc


def describe_cert(cert) -> str:
    from cryptography.x509.oid import NameOID

    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(names[0].value) if names else cert.subject.rfc4514_string()


def verify(token: bytes, digest: bytes, cert_pem: bytes) -> datetime:
    """Check a kept token: over `digest`, signed with the key of the TSA certificate `cert_pem`
    (which must be meant for time-stamping and valid at the time it attests). Returns that time, in
    UTC; raises TimestampError with the reason it does not hold."""
    rfc, _ = _lib()
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

    got = parse(token)
    if got["hash"] != HASH or got["imprint"] != digest:
        raise TimestampError("digest", "the token is over another hash than the line's")
    cert = load_cert(cert_pem)
    try:
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        stamping = any(u.dotted_string == ID_KP_TIME_STAMPING for u in usage)
    except x509.ExtensionNotFound:
        stamping = False
    if not stamping:
        raise TimestampError("certificate", "the certificate is not meant for time-stamping (no timeStamping usage)")
    if not isinstance(cert.public_key(), RSAPublicKey):
        raise TimestampError("certificate", "only RSA TSA certificates can be checked here")
    when = got["time"]
    if not cert.not_valid_before_utc <= when <= cert.not_valid_after_utc:
        raise TimestampError("certificate", "the certificate was not valid at the time the token attests")
    pem = cert.public_bytes(serialization.Encoding.PEM)
    try:
        rfc.check_timestamp(token, certificate=pem, digest=digest, hashname=HASH)
    except InvalidSignature as exc:
        raise TimestampError("signature", "the token's signature does not hold under the certificate") from exc
    except Exception as exc:            # a token whose signed parts do not add up
        raise TimestampError("signature", f"the token's signature could not be checked ({exc})"[:300]) from exc
    return when
