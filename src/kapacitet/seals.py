"""Seals: every answer and every decision, made tamper-evident.

A DSO that limits a house's export has to show later what it decided and on what grounds. When an
assessment, a queue decision, an emptying of the queue or a change of the DSO's settings is kept,
it is sealed here:

- its canonical JSON (keys sorted, fixed separators, non-finite numbers written as null) is hashed
  with SHA-256, and that exact text is stored as an immutable copy in
  `data/requests/sealed/<kind>/<id>.json`;
- a line is appended to `data/requests/seals.jsonl` with the digest, the fingerprint of the model
  and data it was computed with, and a chain hash over the previous line, so removing, reordering
  or editing any line breaks every line after it.

Every new line is also signed. The installation keeps one Ed25519 key (`key_dir()`, by default
KAPACITET_HOME/keys/seal_ed25519.pem, created on first use and readable only by its owner where the
OS allows); each line carries `key_id`, the SHA-256 of the raw public key, and `sig`, the key's
signature over the SHA-256 hex digest of the line's canonical JSON without `sig`. Its public key is
published next to every chain as `seal_key.pub`. Whoever can write the folder can still rebuild the
hashes, but not the signatures: a rewrite shows to anyone who holds the public key. Lines sealed
before signing began are legacy: `verify_chain` counts them and says so, and does not fail them;
an unsigned line after the first signed one (`signed_since`) fails. `rotate_key` replaces the key
with a rotation record signed by the old and the new one, so a verifier who trusts either trusts
both. A signature is not a trusted timestamp: the time in a line is the server's own, and cutting
lines off the end of the chain leaves the rest valid, so writing the chain head somewhere else (a
printed record, an e-mail to the regulator) is still what anchors it. The canonical form is this
module's, not RFC 8785.

A crash never leaves half a record: the copy is written atomically, and each chain line is one
fsynced append (kapacitet.atomic). A line cut short by a crash during its append is a torn tail:
the reader leaves it out, `verify_chain` reports it, and the next `seal` cuts it off before it
appends. A record that must not be kept any longer (a retention period, a request to delete it) is
erased with `erase`: its copy goes, and an `erasure` record is sealed in its place, so the chain,
which holds only digests, stays whole and still says that the record existed and when it went.

That is the files backend (`FolderChain`). With KAPACITET_DATABASE_URL set, the server's own chain
is kept in the database instead (kapacitet.store, store.postgres.Chain): one append-only row per
line, with its signature in the same row, the canonical copies and fingerprints beside it, and
each seal one transaction under an advisory lock. A chain opened with `chain_at` (a study's
evidence folder) is always a folder. `kapacitet db export-files` writes the database's chain back
as these files, byte for byte, for `kapacitet seals verify`.

The standard library and `cryptography` (for Ed25519) only, so the engine, the API and the tests
can all use it. The private key never leaves `key_dir()`: no bundle, manifest, log or publication
holds it, and no message names more of it than its path.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import math
import os
import subprocess
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import __version__, store
from .atomic import append_line, write_bytes_atomic, write_json_atomic, write_text_atomic
from .paths import CONFIG, DATA, PROCESSED, RAW, SEAL_KEYS

_LOCK = threading.RLock()
GENESIS = "0" * 64
KINDS = ("assessment", "decision", "queue_reset", "settings", "application", "schedule", "eipack", "erasure",
         "publication", "sync", "rule_signature", "cases", "market_alternatives", "key_rotation")
# a chain kept somewhere else than data/requests: a study's evidence/ folder (`chain_at`)
_CHAIN_AT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("kapacitet_seal_chain", default=None)

# What an answer depends on, besides the engine's code: the network, the profiles behind it, the
# published results and the inputs. A change to any of these changes the fingerprint. The "study"
# root is the study folder (KAPACITET_STUDY) of a station's process, and is absent elsewhere;
# "{station}" is that station's id (the name of its data folder).
FINGERPRINT_FILES = (
    ("processed", "grid/net.json"),
    ("processed", "grid/customers.parquet"),
    ("processed", "pv/roofs.parquet"),
    ("processed", "request/doe.json"),
    ("processed", "results/meta.json"),
    ("processed", "results/showcase.json"),
    ("processed", "results/operation/index.json"),
    ("processed", "meters/hourly.parquet"),
    ("raw", "manifest.json"),
    ("config", "assumptions.toml"),
    ("config", "site.toml"),
    ("study", "study.toml"),
    ("study", "inputs/stations.csv"),
    ("study", "inputs/equipment.csv"),
    ("study", "inputs/upstream.csv"),
    ("study", "inputs/mv/{station}.csv"),
)


def folder() -> Path:
    return DATA / "requests"


def _root() -> Path:
    """Where the chain in use lives: the folder of `chain_at`, or data/requests."""
    return _CHAIN_AT.get() or folder()


@contextmanager
def chain_at(path: str | Path):
    """Seal into, and read from, the chain in `path` instead of data/requests: a study's sign-off
    is chained in the study's own evidence/ folder (study/publish.py), apart from any server's."""
    token = _CHAIN_AT.set(Path(path))
    try:
        yield Path(path)
    finally:
        _CHAIN_AT.reset(token)


class FolderChain:
    """The files backend of a chain: seals.jsonl, sealed/<kind>/<id>.json, sealed/fingerprints/
    and seal_key.pub in the folder `root`. store.postgres.Chain is the same interface on the
    database, for the server's own chain when KAPACITET_DATABASE_URL is set."""

    backend = "files"

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def file(self) -> Path:
        return self.root / "seals.jsonl"

    @property
    def name(self) -> str:
        """What this installation's signed-line marks are kept under (`_note_signed`)."""
        return str(self.file)

    def where(self) -> str:
        return str(self.file)

    def locked(self):
        return nullcontext()          # one server writes a folder, under _LOCK

    def exists(self) -> bool:
        return self.file.is_file()

    def read(self) -> tuple[list[dict], int | None, bool]:
        """The chain's lines; the byte offset where a torn last line starts (None when there is
        none); and whether the file lacks its final newline after a whole last line.

        A last line without its newline that does not parse is torn: an append cut short. One that
        parses lost only its newline and counts. A damaged line anywhere else is kept as an empty
        entry, so the chain breaks there instead of skipping it.
        """
        path = self.file
        if not path.exists():
            return [], None, False
        data = path.read_bytes()
        out: list[dict] = []
        pos = 0
        for raw in data.splitlines(keepends=True):
            start, pos = pos, pos + len(raw)
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                entry = json.loads(text)
            except ValueError:
                entry = None
            last = pos == len(data) and not raw.endswith((b"\n", b"\r"))
            if isinstance(entry, dict):
                out.append(entry)
                if last:
                    return out, None, True
            elif last:
                return out, start, False
            else:
                out.append({})
        return out, None, False

    def mend(self) -> list[dict]:
        """Cut off a torn last line, or put back a lost final newline, so the next append starts a
        line of its own; return the chain's lines."""
        lines, torn, unterminated = self.read()
        if torn is None and not unterminated:
            return lines
        with self.file.open("r+b") as fh:
            if torn is not None:
                fh.truncate(torn)
            else:
                fh.seek(0, os.SEEK_END)
                fh.write(b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        return lines

    def append(self, entry: dict) -> None:
        append_line(self.file, json.dumps(entry, sort_keys=True))

    def _copy(self, kind: str, item_id: str) -> Path:
        return self.root / "sealed" / kind / f"{item_id}.json"

    def copy_get(self, kind: str, item_id: str) -> bytes | None:
        path = self._copy(kind, item_id)
        return path.read_bytes() if path.exists() else None

    def copy_put(self, kind: str, item_id: str, text: bytes) -> None:
        write_bytes_atomic(self._copy(kind, item_id), text)

    def copy_delete(self, kind: str, item_id: str) -> None:
        self._copy(kind, item_id).unlink(missing_ok=True)

    def fingerprint_put(self, fp_digest: str, text: str) -> None:
        path = self.root / "sealed" / "fingerprints" / f"{fp_digest}.json"
        if not path.exists():
            write_text_atomic(path, text)

    def published_key(self) -> bytes | None:
        path = self.root / PUBLISHED
        return path.read_bytes() if path.is_file() else None

    def publish_key(self, pem: str) -> None:
        _write_if_changed(self.root / PUBLISHED, pem)


def _chain():
    """The chain in use: the folder of `chain_at`; else the server's own, in data/requests or, with
    KAPACITET_DATABASE_URL, in the database (kapacitet.store)."""
    at = _CHAIN_AT.get()
    if at is None and store.is_pg():
        from .store import postgres

        return postgres.Chain()
    return FolderChain(_root())


def _clean(value: Any) -> Any:
    """The record as JSON can hold it: non-finite numbers become null, tuples become lists."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def canonical(obj: Any) -> bytes:
    """The one text a record is hashed as."""
    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_FILE_CACHE: dict[str, tuple[int, int, str]] = {}


def fingerprint() -> dict:
    """The engine version and a hash of each input file there is, cached by size and mtime."""
    roots = {"processed": PROCESSED, "raw": RAW, "config": CONFIG}
    if os.environ.get("KAPACITET_STUDY"):
        roots["study"] = Path(os.environ["KAPACITET_STUDY"])
    files: dict[str, str] = {}
    for root, rel in FINGERPRINT_FILES:
        if root not in roots:
            continue
        rel = rel.format(station=DATA.name) if root == "study" else rel
        path = roots[root] / rel
        if not path.exists():
            continue
        st = path.stat()
        key = str(path)
        cached = _FILE_CACHE.get(key)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            files[f"{root}/{rel}"] = cached[2]
            continue
        sha = _sha256_file(path)
        _FILE_CACHE[key] = (st.st_size, st.st_mtime_ns, sha)
        files[f"{root}/{rel}"] = sha
    combined = hashlib.sha256("\n".join(f"{k}:{v}" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"engine": __version__, "files": files, "digest": combined}


def _read_chain() -> tuple[list[dict], int | None, bool]:
    """The chain's lines, where a torn last line starts and whether the final newline is missing
    (FolderChain.read; the database's chain has neither)."""
    return _chain().read()


def _lines() -> list[dict]:
    """Every whole line of the chain, oldest first (a torn last line left out)."""
    return _read_chain()[0]


def _mend_tail() -> list[dict]:
    """Make sure the next append starts a line of its own; return the chain's lines."""
    return _chain().mend()


def _chain_hash(prev: str, entry: dict) -> str:
    body = {k: entry[k] for k in ("seq", "at", "kind", "id", "digest", "fingerprint")}
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


# ---------------------------------------------------------------- the signing key

ALGORITHM = "Ed25519"
KEY_FILE = "seal_ed25519.pem"          # the private key, in key_dir() only
PUBLIC_FILE = "seal_ed25519.pub"       # its public key, beside it
PUBLISHED = "seal_key.pub"             # the public key, next to every chain that holds a line it signed
ROTATION_SCHEMA = "kapacitet.seal-key-rotation/1"
_ROTATION_FIELDS = ("schema", "algorithm", "at", "old_key_id", "old_public_key", "new_key_id", "new_public_key")
# The sentence a published file carries next to the public key.
HOW_TO_VERIFY = {
    "en": ("Each chain line's sig is an Ed25519 signature, in base64, over the SHA-256 hex digest of the "
           "line's canonical JSON without sig (keys sorted, separators ',' and ':', UTF-8), and key_id is the "
           "SHA-256 of the raw 32-byte public key; `kapacitet seals verify <folder> --key seal_key.pub` "
           "checks the hash chain and every signature."),
    "sv": ("Varje kedjerads sig är en Ed25519-signatur, i base64, över SHA-256-summan (hex) av radens "
           "kanoniska JSON utan sig (sorterade nycklar, avgränsarna ',' och ':', UTF-8), och key_id är "
           "SHA-256-summan av den råa publika nyckeln på 32 byte; `kapacitet seals verify <mapp> --key "
           "seal_key.pub` kontrollerar hashkedjan och varje signatur."),
}
_KEYS: dict[str, tuple[int, int, Ed25519PrivateKey]] = {}
_SIG_OK: dict[tuple[str, bytes, str], bool] = {}


class SealKeyError(ValueError):
    """The signing key cannot be read, or there is none to rotate."""


def key_dir() -> Path:
    """Where the installation's signing key lives (KAPACITET_SEAL_KEY_DIR, HOME/keys by default)."""
    return SEAL_KEYS


def _raw(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_of(pub: Ed25519PublicKey) -> str:
    """A key's id: the SHA-256 of its raw 32 bytes."""
    return hashlib.sha256(_raw(pub)).hexdigest()


def public_pem(pub: Ed25519PublicKey) -> str:
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def load_public(text: str | bytes) -> Ed25519PublicKey | None:
    """An Ed25519 public key from its PEM, or None when the text is not one."""
    try:
        key = serialization.load_pem_public_key(text.encode("ascii") if isinstance(text, str) else text)
    except (ValueError, TypeError, UnicodeEncodeError):
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def _b64(sig: bytes) -> str:
    return base64.b64encode(sig).decode("ascii")


def _restrict(path: Path) -> None:
    """Only the owner may read the file, where the OS allows it: mode 0600 on POSIX; on Windows an
    access list with the current user alone (icacls, best effort: a file system without access
    lists keeps the folder's)."""
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    domain = os.environ.get("USERDOMAIN")
    who = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{who}:F"],
                       capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_private(path: Path, data: bytes, replace: bool) -> bool:
    """Write a private key that only its owner can read. A new key (`replace` false) never takes the
    place of one another process wrote first: then this returns False and that one stands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _restrict(tmp)
        if replace:
            os.replace(tmp, path)
            return True
        try:
            os.link(tmp, path)          # never over a key that is already there
        except FileExistsError:
            return False
        except OSError:                 # a file system without hard links
            if path.exists():
                return False
            os.replace(tmp, path)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _write_if_changed(path: Path, text: str) -> None:
    try:
        if path.read_text(encoding="ascii") == text:
            return
    except (OSError, UnicodeDecodeError):
        pass
    write_text_atomic(path, text, encoding="ascii")


def signing_key(create: bool = True) -> Ed25519PrivateKey | None:
    """The installation's private key, created on first use (None when there is none and `create`
    is false). Its public key is kept beside it as seal_ed25519.pub."""
    path = key_dir() / KEY_FILE
    with _LOCK:
        if not path.exists():
            if not create:
                return None
            _write_private(path, _private_pem(Ed25519PrivateKey.generate()), replace=False)
        st = path.stat()
        cached = _KEYS.get(str(path))
        if cached and cached[:2] == (st.st_size, st.st_mtime_ns):
            return cached[2]
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (ValueError, TypeError):    # never the parser's own words: they could quote the file
            raise SealKeyError(f"{path} is not a readable signing key") from None
        if not isinstance(key, Ed25519PrivateKey):
            raise SealKeyError(f"{path} is not an Ed25519 key")
        _KEYS[str(path)] = (st.st_size, st.st_mtime_ns, key)
        _write_if_changed(key_dir() / PUBLIC_FILE, public_pem(key.public_key()))
        return key


def local_keys() -> dict[str, Ed25519PublicKey]:
    """{key id: public key} of this installation's key and of every key it retired."""
    key = signing_key(create=False)
    out = {key_id_of(key.public_key()): key.public_key()} if key else {}
    for path in sorted((key_dir() / "retired").glob("*.pub")):
        pub = load_public(path.read_bytes())
        if pub is not None:
            out.setdefault(key_id_of(pub), pub)
    return out


def public_key_info(key_id: str | None = None, create: bool = False) -> dict:
    """What a published file says about the key: algorithm, id, public key (PEM) and how to verify.
    Without `key_id`, the key in use; with one, that key if this installation holds it."""
    if key_id is None:
        key = signing_key(create=create)
        pub = key.public_key() if key else None
    else:
        pub = local_keys().get(key_id)
    return {"algorithm": ALGORITHM, "key_id": key_id_of(pub) if pub else key_id,
            "public_key": public_pem(pub) if pub else None, "how_to_verify": HOW_TO_VERIFY}


def _line_message(entry: dict) -> bytes:
    """What a line's signature covers: the hex digest of its canonical JSON without `sig`."""
    return digest({k: v for k, v in entry.items() if k != "sig"}).encode("ascii")


def _sig_valid(pub: Ed25519PublicKey, kid: str, entry: dict) -> bool:
    sig = entry.get("sig")
    if not isinstance(sig, str):
        return False
    msg = _line_message(entry)
    memo = (kid, msg, sig)
    hit = _SIG_OK.get(memo)
    if hit is None:
        try:
            pub.verify(base64.b64decode(sig, validate=True), msg)
            hit = True
        except (InvalidSignature, ValueError):
            hit = False
        if len(_SIG_OK) > 100_000:
            _SIG_OK.clear()
        _SIG_OK[memo] = hit
    return hit


def _rotation_id(rec: dict) -> str:
    return str(rec["new_key_id"])[:16]


def rotation_valid(rec: Any) -> tuple[str, str, Ed25519PublicKey, Ed25519PublicKey] | None:
    """(old id, new id, old key, new key) of a rotation record signed by both keys, else None."""
    try:
        statement = {k: rec[k] for k in _ROTATION_FIELDS}
        old, new = load_public(rec["old_public_key"]), load_public(rec["new_public_key"])
        if rec["schema"] != ROTATION_SCHEMA or old is None or new is None:
            return None
        if key_id_of(old) != rec["old_key_id"] or key_id_of(new) != rec["new_key_id"]:
            return None
        msg = digest(statement).encode("ascii")
        old.verify(base64.b64decode(rec["sig_old"], validate=True), msg)
        new.verify(base64.b64decode(rec["sig_new"], validate=True), msg)
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return None
    return rec["old_key_id"], rec["new_key_id"], old, new


def _local_rotations() -> dict[str, dict]:
    """{new key id: rotation record} of every rotation this installation made."""
    out = {}
    for path in sorted((key_dir() / "rotations").glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rotation_valid(rec):
            out[rec["new_key_id"]] = rec
    return out


def _publish_key(pub: Ed25519PublicKey) -> None:
    """seal_key.pub next to the chain in use: the key of its latest line."""
    _chain().publish_key(public_pem(pub))


def _safe_id(item_id: str) -> bool:
    return bool(item_id) and "/" not in item_id and "\\" not in item_id and not item_id.startswith(".")


def _check_id(kind: str, item_id: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown seal kind {kind!r}")
    if not _safe_id(item_id):
        raise ValueError(f"bad seal id {item_id!r}")


def _append(kind: str, item_id: str, text: bytes, fp: dict, lines: list[dict], key: Ed25519PrivateKey) -> dict:
    """Keep a record's canonical text and append its signed line to the chain in use (under _LOCK,
    `lines` being the chain as it stands, which this extends)."""
    dig = hashlib.sha256(text).hexdigest()
    ch = _chain()
    kept = ch.copy_get(kind, item_id)
    if kept is not None:
        if kept != text:
            raise ValueError(f"{kind} {item_id} is already sealed with different content")
    else:
        ch.copy_put(kind, item_id, text)
    prev = lines[-1].get("chain", GENESIS) if lines else GENESIS
    seq = len(lines) + 1
    # where signing began in this chain: every line from there on must be signed (verify_chain)
    since = next((e.get("signed_since") for e in reversed(lines) if e.get("sig")), None)
    entry = {"seq": seq, "at": datetime.now(timezone.utc).isoformat(), "kind": kind,
             "id": item_id, "digest": dig, "fingerprint": fp["digest"], "prev": prev}
    entry["chain"] = _chain_hash(prev, entry)
    entry["key_id"] = key_id_of(key.public_key())
    entry["signed_since"] = since if isinstance(since, int) and 1 <= since < seq else seq
    entry["sig"] = _b64(key.sign(_line_message(entry)))
    ch.append(entry)
    lines.append(entry)
    _note_signed(sum(1 for e in lines if e.get("sig")))
    # the fingerprint's files are kept once per distinct fingerprint, so a verifier can say what changed
    ch.fingerprint_put(fp["digest"], json.dumps(fp, indent=1, sort_keys=True))
    _publish_key(key.public_key())
    return entry


def _marks_file() -> Path:
    return key_dir() / "signed_marks.json"


def _read_marks() -> dict:
    try:
        got = json.loads(_marks_file().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _note_signed(count: int) -> None:
    """Keep, next to the key and outside the data, how many signed lines this chain has had, so a
    rewrite that strips every signature (and rebuilds the hashes) is caught by this installation."""
    marks = _read_marks()
    name = _chain().name
    if count > marks.get(name, 0):
        marks[name] = count
        try:
            write_json_atomic(_marks_file(), marks, sort_keys=True)
        except OSError:
            pass


def _carry_rotations(lines: list[dict], key: Ed25519PrivateKey, fp: dict) -> list[dict]:
    """A chain whose last signed line is by a key this installation has since rotated gets the
    rotation records that lead from that key to the one in use first, so a verifier who trusts the
    older key comes to trust the newer one (and the other way round)."""
    current = key_id_of(key.public_key())
    last = next((e.get("key_id") for e in reversed(lines) if e.get("sig")), None)
    if last is None or last == current:
        return lines
    rotations, path, kid = _local_rotations(), [], current
    while kid != last and kid in rotations and len(path) < 256:
        path.append(rotations[kid])
        kid = rotations[kid]["old_key_id"]
    if kid != last:      # no record of this installation joins the two keys: verify says which line
        return lines
    have = {e.get("id") for e in lines if e.get("kind") == "key_rotation"}
    for rec in reversed(path):
        if _rotation_id(rec) not in have:
            _append("key_rotation", _rotation_id(rec), canonical(rec), fp, lines, key)
    return lines


def seal(kind: str, item_id: str, obj: Any) -> dict:
    """Seal one record: keep its canonical text and chain its digest, signed with the installation's
    key. Returns the chain entry."""
    _check_id(kind, item_id)
    text = canonical(obj)
    fp = fingerprint()
    with _LOCK, _chain().locked():
        key = signing_key()
        lines = _carry_rotations(_mend_tail(), key, fp)
        return _append(kind, item_id, text, fp, lines, key)


def rotate_key(chain: str | Path | None = None) -> dict:
    """Replace the installation's key with a new one. The rotation record names both public keys
    and is signed by both; it is kept in key_dir()/rotations/, the old public key in
    key_dir()/retired/, and it is sealed into the chain in `chain` (data/requests by default). Any
    other chain gets it on its next seal. The old private key is gone afterwards."""
    with _LOCK:
        old = signing_key(create=False)
        if old is None:
            raise SealKeyError(f"no signing key in {key_dir()} yet: the first seal creates one")
        new = Ed25519PrivateKey.generate()
        statement = {"schema": ROTATION_SCHEMA, "algorithm": ALGORITHM,
                     "at": datetime.now(timezone.utc).isoformat(),
                     "old_key_id": key_id_of(old.public_key()), "old_public_key": public_pem(old.public_key()),
                     "new_key_id": key_id_of(new.public_key()), "new_public_key": public_pem(new.public_key())}
        msg = digest(statement).encode("ascii")
        rec = {**statement, "sig_old": _b64(old.sign(msg)), "sig_new": _b64(new.sign(msg))}
        folder = key_dir()
        _write_if_changed(folder / "retired" / f"{statement['old_key_id']}.pub", statement["old_public_key"])
        write_json_atomic(folder / "rotations" / f"{statement['new_key_id']}.json", rec, sort_keys=True)
        _write_private(folder / KEY_FILE, _private_pem(new), replace=True)
        _KEYS.pop(str(folder / KEY_FILE), None)
        key = signing_key(create=False)
        assert key is not None and key_id_of(key.public_key()) == statement["new_key_id"]
        with chain_at(chain) if chain is not None else nullcontext(), _chain().locked():
            fp = fingerprint()
            lines = _carry_rotations(_mend_tail(), key, fp)
            entry = next((e for e in lines if e.get("kind") == "key_rotation" and e.get("id") == _rotation_id(rec)), None)
            if entry is None:
                entry = _append("key_rotation", _rotation_id(rec), canonical(rec), fp, lines, key)
    return {"old_key_id": statement["old_key_id"], "new_key_id": statement["new_key_id"], "entry": entry}


def entry_for(kind: str, item_id: str) -> dict | None:
    """The latest chain entry of a record, or None if it was never sealed."""
    found = None
    for e in _lines():
        if e.get("kind") == kind and e.get("id") == item_id:
            found = e
    return found


def _trust(trusted: dict[str, Ed25519PublicKey] | None) -> tuple[dict[str, Ed25519PublicKey], str]:
    """The keys a verification starts from, and where they came from: the ones given (a published
    seal_key.pub); else this installation's; else, for a chain copied elsewhere, the seal_key.pub in
    its own folder, which a rewrite of the folder could have replaced too (the result says so)."""
    if trusted is not None:
        return dict(trusted), "given"
    mine = local_keys()
    if mine:
        return mine, "installation"
    text = _chain().published_key()
    pub = load_public(text) if text is not None else None
    return ({key_id_of(pub): pub} if pub else {}), "folder"


def verify_chain(trusted: dict[str, Ed25519PublicKey] | None = None, require_signed: bool = False) -> dict:
    """Every line follows from the one before it, and every signed line's signature holds under a
    trusted key. `torn_tail` says that the last append was cut short by a crash; the lines before
    it still count, and the next seal cuts it off.

    `trusted` is {key id: public key} to start from (see `_trust` for the default); a rotation
    record in the chain, signed by both its keys, extends the trust from either key to the other.
    Lines from before signing began are `legacy`: counted and warned about, not failures, unless
    `require_signed`. `reason` says why a chain fails: "chain" (a hash or order broken),
    "signature", "unknown_key" (signed by a key not trusted) or "unsigned" (a line without a
    signature where every line must have one)."""
    lines, torn, _ = _read_chain()
    known, source = _trust(trusted)
    rotations = []
    for e in lines:
        rid = e.get("id")
        if e.get("kind") == "key_rotation" and isinstance(rid, str) and _safe_id(rid):
            rec = sealed_copy("key_rotation", rid)
            got = rotation_valid(rec) if rec is not None and digest(rec) == e.get("digest") else None
            if got:
                rotations.append(got)
    grew = True
    while grew:
        grew = False
        for old_id, new_id, old, new in rotations:
            if (old_id in known) != (new_id in known):
                known.setdefault(old_id, old)
                known.setdefault(new_id, new)
                grew = True
    head = lines[-1].get("chain", GENESIS) if lines else GENESIS
    base = {"length": len(lines), "head": head, "torn_tail": torn is not None, "trust": source}

    def broken(at: int, reason: str, signed: int, legacy: int, keys: set) -> dict:
        return {**base, "ok": False, "broken_at": at, "reason": reason, "signed": signed, "legacy": legacy,
                "keys": sorted(keys), "warnings": []}

    prev, signed, legacy, last_unsigned, keys = GENESIS, 0, 0, 0, set()
    for i, e in enumerate(lines, start=1):
        try:
            ok = e.get("seq") == i and e.get("prev") == prev and _chain_hash(prev, e) == e.get("chain")
        except KeyError:      # a line without the fields a chain line has
            ok = False
        if not ok:
            return broken(i, "chain", signed, legacy, keys)
        if "sig" not in e and "key_id" not in e and "signed_since" not in e:
            if signed or require_signed:
                return broken(i, "unsigned", signed, legacy, keys)
            legacy, last_unsigned = legacy + 1, i
        else:
            kid = e.get("key_id")
            pub = known.get(kid) if isinstance(kid, str) else None
            if pub is None:
                return broken(i, "unknown_key", signed, legacy, keys)
            if not _sig_valid(pub, kid, e):
                return broken(i, "signature", signed, legacy, keys)
            since = e.get("signed_since")
            if not isinstance(since, int) or since > i or last_unsigned >= since:
                # a line from where signing began has lost its signature
                return broken(last_unsigned or i, "unsigned", signed, legacy, keys)
            signed += 1
            keys.add(kid)
        prev = e["chain"]
    if source == "installation" and signed < _read_marks().get(_chain().name, 0):
        # this installation signed more lines than the chain holds now: signatures were stripped
        return broken(1, "unsigned", signed, legacy, keys)
    warnings = []
    if legacy:
        warnings.append("legacy_unsigned")
    if source == "folder" and signed:
        warnings.append("key_from_folder")
    return {**base, "ok": True, "broken_at": None, "head": prev, "reason": None, "signed": signed,
            "legacy": legacy, "keys": sorted(keys), "warnings": warnings}


def _erasure_id(kind: str, item_id: str) -> str:
    return f"{kind}.{item_id}"


def _erasure_of(kind: str, item_id: str) -> dict | None:
    """The erasure of a record's latest seal, or None when that seal stands."""
    entry = entry_for(kind, item_id)
    gone = entry_for("erasure", _erasure_id(kind, item_id))
    if entry is None or gone is None or gone.get("seq", 0) < entry.get("seq", 0):
        return None
    return gone


def erased(kind: str, item_id: str) -> bool:
    return _erasure_of(kind, item_id) is not None


def erase(kind: str, item_id: str, reason: str) -> dict:
    """Remove a sealed record's copy and seal its erasure: the kind, the id, the digest it had and
    why it went. The chain keeps every line, so it stays valid; `verify` then says `erased`.
    Erasing an erased record again only makes sure its copy is gone."""
    if kind not in KINDS or kind in ("erasure", "key_rotation"):   # a rotation is what later lines' trust rests on
        raise ValueError(f"cannot erase a seal of kind {kind!r}")
    with _LOCK, _chain().locked():
        entry = entry_for(kind, item_id)
        if entry is None:
            raise ValueError(f"{kind} {item_id} has no seal")
        done = _erasure_of(kind, item_id)
        if done is None:
            done = seal("erasure", _erasure_id(kind, item_id),
                        {"kind": kind, "id": item_id, "digest": entry["digest"], "seq": entry["seq"],
                         "reason": str(reason)[:200]})
        _chain().copy_delete(kind, item_id)
    return done


def verify(kind: str, item_id: str, current: Any = None) -> dict:
    """Whether a sealed record is intact: its kept copy hashes to the chained digest, the chain
    holds (hashes and signatures), and (when given) the working record still says the same as the
    copy. `signed` says whether its own line is signed (a legacy line is not)."""
    entry = entry_for(kind, item_id)
    if entry is None:
        return {"sealed": False}
    chain = verify_chain()
    out = {"sealed": True, "kind": kind, "id": item_id, "digest": entry["digest"], "sealed_at": entry["at"],
           "seq": entry["seq"], "fingerprint": entry["fingerprint"],
           "signed": "sig" in entry, "key_id": entry.get("key_id"),
           "chain_ok": chain["ok"], "chain_length": chain["length"], "chain_head": chain["head"],
           "chain_reason": chain["reason"], "chain_warnings": chain["warnings"], "erased": False}
    gone = _erasure_of(kind, item_id)
    if gone is not None:     # nothing left to compare: the chain says it existed and when it went
        why = sealed_copy("erasure", _erasure_id(kind, item_id)) or {}
        out.update(erased=True, erased_at=gone.get("at"), erasure_seq=gone.get("seq"), reason=why.get("reason"),
                   copy_ok=None, ok=bool(chain["ok"]))
        return out
    kept = _chain().copy_get(kind, item_id)
    copy_ok = kept is not None and hashlib.sha256(kept).hexdigest() == entry["digest"]
    out["copy_ok"] = bool(copy_ok)
    if current is not None:
        out["record_ok"] = digest(current) == entry["digest"]
    out["ok"] = bool(copy_ok and chain["ok"] and out.get("record_ok", True))
    return out


def sealed_copy(kind: str, item_id: str) -> Any | None:
    kept = _chain().copy_get(kind, item_id)
    return json.loads(kept.decode("utf-8")) if kept is not None else None


def sealed_of_kind(kind: str) -> list[dict]:
    """Every sealed record of a kind with its chain entry, oldest first."""
    out = []
    for e in _lines():
        if e.get("kind") != kind:
            continue
        rec = sealed_copy(kind, e["id"])
        if rec is not None:
            out.append({"entry": e, "record": rec})
    return out
