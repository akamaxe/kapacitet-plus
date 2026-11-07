"""One installation per DSO: check it, back it up and restore it (`kapacitet instance`).

Kapacitet+ is single-tenant. A DSO runs its own installation: the container image, its own data
volume, its own PostgreSQL database (or the files backend under data/requests), its own app at
its identity provider, its own seal key and its own TSA settings. Nothing is shared between two
DSOs, so nothing here knows of tenants. docs/deployment.md is the operator's guide and
deploy/instance.env.example lists every variable.

`check` reads the environment and the folders and says, piece by piece, whether the instance can
run as configured: the data folder and its results, the database reachable and migrated, sign-in
configured or off on purpose, the names the server answers to, the seal key and its key id, the
TSA settings, telemetry and the free disk. Each finding is `ok`, `warn` or `error`; the command
exits 1 on any error. It contacts nothing but the database, and it never prints a secret: a URL
is shown without its password, a key by its id, the provider by the host of its issuer.

`backup <dir>` writes, into an empty or new folder:

    MANIFEST.json        what was taken, when, from which backend and scope, and the SHA-256 of
                         every file below
    database.dump        pg_dump --format=custom of the whole database (PostgreSQL backend), a
                         consistent snapshot taken while the server runs
    requests/            the files backend's state (data/requests without its cache and the
                         server lock), taken under the server lock: a running server is stopped
                         first, so no write lands halfway through the copy
    SECRET-seal-key/     the installation's signing key, its retired keys and its counts of
                         signed lines, with KEY-MANIFEST.json and a README saying it is secret
                         (`--key-out` writes it elsewhere, `--no-key` leaves it out)

`restore <dir>` puts a backup into an empty instance: no state in the data folder (or an empty
database) and no signing key. Every digest is checked before anything is written, and the
restored chain is verified afterwards with the restored keys.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .atomic import write_bytes_atomic, write_json_atomic, write_text_atomic

SCHEMA = "kapacitet.instance-backup/1"
MANIFEST = "MANIFEST.json"
DUMP = "database.dump"
REQUESTS = "requests"
SECRET_DIR = "SECRET-seal-key"
KEY_MANIFEST = "KEY-MANIFEST.json"
KEY_README = "README-SECRET.txt"
# what data/requests holds that is not state: a cache of injections and the running server's lock
SKIP_TOP = {"cache", "server.lock"}
# free disk under the data folder: below the first is an error, below the second a warning
DISK_ERROR_BYTES = 1 << 30
DISK_WARN_BYTES = 5 << 30
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")

_SECRET_README = """THIS FOLDER IS SECRET.

It holds the private Ed25519 key that signs every line of this Kapacitet+ installation's seal
chain (seal_ed25519.pem), the public keys it retired and its counts of signed lines. Whoever holds
the private key can sign lines as this installation. Keep it apart from the rest of the backup,
encrypted and readable only by the people who run the installation, as you keep the database's
password.

`kapacitet instance restore <backup> --key-from <this folder>` puts it back into an empty
instance. KEY-MANIFEST.json lists the files and their SHA-256; the key id is the SHA-256 of the
raw public key, as every chain line names it.
"""


class InstanceError(RuntimeError):
    """A backup or a restore cannot go ahead; the message says why and what to do."""


@dataclass
class Finding:
    area: str
    level: str          # "ok", "warn" or "error"
    message: str


@dataclass
class Instance:
    """The folders an installation runs on; `current()` is the one the environment names."""
    data: Path
    requests: Path
    keys: Path

    @classmethod
    def current(cls) -> "Instance":
        from . import paths, seals
        from .api import records

        return cls(data=Path(paths.DATA), requests=Path(records.folder()), keys=Path(seals.key_dir()))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _is_tmp(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(".tmp")


# ================================================================ the database, without its secrets

def describe_url(url: str) -> str:
    """The database a URL names, without its password: user@host:port/dbname."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        p = conninfo_to_dict(url)
    except Exception:  # noqa: BLE001  (psycopg missing, or a URL it cannot read)
        parts = urlsplit(url)
        return f"{parts.username or ''}@{parts.hostname or ''}:{parts.port or ''}{parts.path}"
    return f"{p.get('user', '')}@{p.get('host', 'localhost')}:{p.get('port', '5432')}/{p.get('dbname', '')}"


def _pg_conninfo(url: str) -> tuple[str, dict]:
    """The URL as a conninfo without its password, and an environment carrying it (PGPASSWORD), so
    the password never shows in a process list."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    password = params.pop("password", None)
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = str(password)
    return make_conninfo(**params), env


def pg_tool(name: str) -> str | None:
    """pg_dump or pg_restore: in KAPACITET_PG_BIN when it is set, else on the PATH."""
    folder = _env("KAPACITET_PG_BIN")
    if folder:
        for candidate in (Path(folder) / name, Path(folder) / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which(name)


def _local_host(host: str) -> bool:
    return host in ("", "localhost", "127.0.0.1", "::1") or host.startswith("/")


# ================================================================ the seal key, read without side effects

def _keys_in(folder: Path) -> tuple[dict, str | None, str | None]:
    """({key id: public key} of the key in `folder` and the keys it retired, the id of the key in
    use, an error). Reads only: nothing is created or rewritten."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from . import seals

    keys, current, error = {}, None, None
    pem = folder / seals.KEY_FILE
    if pem.exists():
        try:
            key = serialization.load_pem_private_key(pem.read_bytes(), password=None)
        except (ValueError, TypeError, OSError):
            key, error = None, f"{pem} is not a readable signing key"
        if key is not None and not isinstance(key, Ed25519PrivateKey):
            key, error = None, f"{pem} is not an Ed25519 key"
        if key is not None:
            current = seals.key_id_of(key.public_key())
            keys[current] = key.public_key()
    for path in sorted((folder / "retired").glob("*.pub")):
        pub = seals.load_public(path.read_bytes())
        if pub is not None:
            keys.setdefault(seals.key_id_of(pub), pub)
    return keys, current, error


def _verify_chain(inst: Instance, trusted: dict | None) -> dict:
    """The chain of this instance (the database's, or the one in its requests folder), verified."""
    from . import seals, store

    if store.is_pg():
        return seals.verify_chain(trusted)
    with seals.chain_at(inst.requests):
        return seals.verify_chain(trusted)


# ================================================================ check

def _check_data(inst: Instance, out: list[Finding]) -> None:
    if not inst.data.is_dir():
        out.append(Finding("data", "error", f"the data folder {inst.data} does not exist (KAPACITET_DATA_DIR)"))
        return
    try:
        with tempfile.NamedTemporaryFile(dir=inst.data, prefix=".instance-check.", suffix=".tmp"):
            pass
        out.append(Finding("data", "ok", f"{inst.data}, writable"))
    except OSError:
        out.append(Finding("data", "error", f"the server cannot write {inst.data}: give its user the folder"))
    from .pipeline import git_working_tree

    if git_working_tree(inst.data) is not None:
        out.append(Finding("data", "warn", f"{inst.data} is inside a git working tree: keep a DSO's data outside "
                                           "any checkout (KAPACITET_DATA_DIR, KAPACITET_HOME)"))
    processed = inst.data / "processed"
    missing = [p for p in ("grid/net.json", "results/showcase.json") if not (processed / p).is_file()]
    if missing:
        out.append(Finding("results", "error", f"missing {', '.join(missing)} under {processed}: run "
                                               "`kapacitet all`, or `kapacitet unbundle` the data release"))
    else:
        out.append(Finding("results", "ok", "the network and the published results are there"))
        if not (processed / "results" / "operation" / "index.json").is_file():
            out.append(Finding("results", "warn", "no operation results: run "
                                                  "`kapacitet compute --stage extra --only operation`"))
        if _env("KAPACITET_SETUP_UPLOAD") == "1":
            out.append(Finding("results", "warn", "KAPACITET_SETUP_UPLOAD=1 on an instance that has its data: "
                                                  "turn it off"))


def _check_database(out: list[Finding]) -> bool:
    """Returns whether the state can be read (files, or a reachable and migrated database)."""
    from . import store

    url = store.database_url()
    if not url:
        out.append(Finding("database", "ok", "no database: the state is kept in files under data/requests "
                                             "(KAPACITET_DATABASE_URL turns PostgreSQL on)"))
        return True
    where = describe_url(url)
    if importlib.util.find_spec("psycopg") is None:
        out.append(Finding("database", "error", f"KAPACITET_DATABASE_URL is set ({where}) but psycopg is "
                                                "missing: install the postgres extra"))
        return False
    from .store import migrate

    try:
        got = migrate.status(url)
    except Exception as exc:  # noqa: BLE001  (unreachable, refused, wrong password...)
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        out.append(Finding("database", "error", f"cannot reach {where}: {first}"))
        return False
    if got["changed"]:
        out.append(Finding("database", "error", f"{where}: migrations changed since applied: "
                                                f"{', '.join(f'{v:03d}' for v in got['changed'])}"))
        return False
    if got["pending"]:
        out.append(Finding("database", "error", f"{where}: migrations pending "
                                                f"({', '.join(f'{v:03d}' for v in got['pending'])}): run "
                                                "`kapacitet db migrate`"))
        return False
    applied = [r["version"] for r in got["migrations"] if r["applied"]]
    out.append(Finding("database", "ok", f"{where}: reachable, schema at {max(applied):03d}, scope "
                                         f"{store.scope()}"))
    host = urlsplit(url).hostname or ""
    if not _local_host(host) and "sslmode=verify" not in url and "sslmode=require" not in url:
        out.append(Finding("database", "warn", f"{where} is reached over the network without TLS: add "
                                               "?sslmode=verify-full"))
    if got["unknown"]:
        out.append(Finding("database", "warn", f"applied migrations this code does not know: {got['unknown']} "
                                               "(a newer version ran here)"))
    tool = pg_tool("pg_dump")
    if tool is None:
        out.append(Finding("database", "warn", "pg_dump is not on the PATH (or in KAPACITET_PG_BIN): "
                                               "`kapacitet instance backup` needs it"))
    else:
        client, server = pg_major(tool), _server_major()
        if client and server and client < server:
            out.append(Finding("database", "warn", f"pg_dump {client} is older than the server ({server}) and "
                                                   "will refuse to dump it: install the client tools of version "
                                                   f"{server} or later"))
    return True


def pg_major(tool: str) -> int | None:
    """The major version of a PostgreSQL client tool (`pg_dump (PostgreSQL) 17.3` gives 17)."""
    import re

    try:
        text = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    got = re.search(r"PostgreSQL\)\s*(\d+)", text) or re.search(r"(\d+)\.\d+", text)
    return int(got.group(1)) if got else None


def _server_major() -> int | None:
    from .store import db

    try:
        conn = db.connect()
        try:
            return int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def _check_signin(out: list[Finding]) -> bool | None:
    """Returns whether sign-in is on (None when it is not configured)."""
    from . import auth

    auth._dotenv()
    mode = _env("KAPACITET_AUTH").lower()
    if not mode:
        out.append(Finding("sign-in", "error", "KAPACITET_AUTH is not set: say KAPACITET_AUTH=oidc, or "
                                               "KAPACITET_AUTH=off on purpose"))
        return None
    auth.config.cache_clear()                  # the environment as it is now, not as first read
    try:
        on = auth.enabled()
        cfg = auth.config() if on else None
    except auth.AuthConfigError as exc:   # names variables, never their values
        out.append(Finding("sign-in", "error", str(exc)))
        return None
    finally:
        auth.config.cache_clear()              # nor kept for whatever reads it next in this process
    if not on:
        out.append(Finding("sign-in", "ok", "off on purpose (KAPACITET_AUTH=off): whoever reaches the server "
                                            "can use all of it"))
        return False
    issuer = urlsplit(cfg.issuer).hostname or cfg.issuer
    out.append(Finding("sign-in", "ok", f"on: provider {issuer}, roles from the claim '{cfg.role_claim}'"
                                        + (", mapped" if cfg.role_map else "")))
    if cfg.redirect_uri:
        parts = urlsplit(cfg.redirect_uri)
        if parts.scheme != "https" and (parts.hostname or "") not in ("localhost", "127.0.0.1"):
            out.append(Finding("sign-in", "warn", "KAPACITET_OIDC_REDIRECT_URI is not https: the session "
                                                  "cookie would travel in the clear"))
    return True


def _check_hosts(out: list[Finding], signin: bool | None) -> None:
    names = [h.strip().lower() for h in _env("KAPACITET_ALLOWED_HOSTS").split(",") if h.strip()]
    if "*" in names:
        out.append(Finding("hosts", "error", "KAPACITET_ALLOWED_HOSTS=* turns the host check off: list the "
                                             "names the server is reached by"))
        return
    public = [n for n in names if n not in LOCAL_HOSTS]
    if not public:
        out.append(Finding("hosts", "warn", "the server answers only to localhost: a reverse proxy in front "
                                            "must reach it by that name, or list its public name in "
                                            "KAPACITET_ALLOWED_HOSTS"))
    elif signin is False:
        out.append(Finding("hosts", "error", f"the server answers to {', '.join(public)} with sign-in off: "
                                             "anyone who reaches it can approve; set KAPACITET_AUTH=oidc"))
    else:
        out.append(Finding("hosts", "ok", f"answers to {', '.join(public)} and localhost"))
    if "*" in {o.strip() for o in _env("KAPACITET_CORS_ORIGINS").split(",")}:
        out.append(Finding("hosts", "warn", "KAPACITET_CORS_ORIGINS=* lets any site read the API's answers"))


def _check_seals(inst: Instance, out: list[Finding], state_readable: bool) -> None:
    from . import seals

    keys, current, error = _keys_in(inst.keys)
    if error:
        out.append(Finding("seal key", "error", error))
    elif current:
        retired = len(keys) - 1
        out.append(Finding("seal key", "ok", f"key id {current[:16]} in {inst.keys}"
                                             + (f", {retired} retired" if retired else "")))
        pem = inst.keys / seals.KEY_FILE
        if os.name != "nt" and pem.stat().st_mode & 0o077:
            out.append(Finding("seal key", "warn", f"{pem} can be read by others: chmod 600"))
    if not state_readable or error:
        return
    # the installation's own keys (and its counts of signed lines) when the folder is the one in use
    own = inst.keys.resolve() == Path(seals.key_dir()).resolve()
    try:
        got = _verify_chain(inst, None if own and current else keys)
    except Exception as exc:  # noqa: BLE001
        out.append(Finding("seal chain", "error", f"cannot read the chain: {type(exc).__name__}"))
        return
    if not current:
        if not got["ok"] and got.get("reason") == "unknown_key":
            out.append(Finding("seal key", "error", f"no signing key in {inst.keys}, but the chain holds signed "
                                                    "lines: restore the key (`kapacitet instance restore "
                                                    "--key-from`)"))
            return
        out.append(Finding("seal key", "warn", f"no signing key yet in {inst.keys}: one is made at the first "
                                               "seal; back it up then"))
    if not got["length"]:
        out.append(Finding("seal chain", "ok", "no lines yet"))
        return
    if not got["ok"]:
        why = {"chain": "the hash chain is broken", "signature": "a signature does not hold",
               "unknown_key": "signed by a key this installation does not hold",
               "unsigned": "a line has lost its signature"}.get(got.get("reason"), got.get("reason"))
        out.append(Finding("seal chain", "error", f"not valid at line {got.get('broken_at')}: {why}"))
        return
    out.append(Finding("seal chain", "ok", f"{got['length']} lines, {got['signed']} signed, valid"))
    if got["legacy"]:
        out.append(Finding("seal chain", "warn", f"{got['legacy']} legacy lines from before signing: held by "
                                                 "the hash chain only"))
    if got["torn_tail"]:
        out.append(Finding("seal chain", "warn", "the last line was cut short by a crash; the next seal cuts it off"))


def _check_tsa(inst: Instance, out: list[Finding]) -> None:
    url = _env("KAPACITET_TSA_URL")
    if not url:
        out.append(Finding("timestamps", "ok", "no TSA (KAPACITET_TSA_URL): the seal lines carry the server's "
                                               "clock and their signature"))
        return
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        out.append(Finding("timestamps", "error", "KAPACITET_TSA_URL is not an http(s) URL"))
        return
    ok = True
    if importlib.util.find_spec("rfc3161ng") is None:
        out.append(Finding("timestamps", "error", "KAPACITET_TSA_URL is set but rfc3161ng is missing: install "
                                                  "the timestamp extra"))
        ok = False
    timeout = _env("KAPACITET_TSA_TIMEOUT_S")
    if timeout:
        try:
            float(timeout)
        except ValueError:
            out.append(Finding("timestamps", "error", "KAPACITET_TSA_TIMEOUT_S is not a number"))
            ok = False
    cert = _env("KAPACITET_TSA_CERT")
    if not cert:
        out.append(Finding("timestamps", "warn", "KAPACITET_TSA_CERT is not set: `kapacitet seals verify` "
                                                 "cannot check the tokens; download the TSA's certificate once"))
    else:
        from cryptography import x509

        try:
            got = x509.load_pem_x509_certificate(Path(cert).read_bytes())
        except (OSError, ValueError):
            out.append(Finding("timestamps", "error", f"KAPACITET_TSA_CERT ({cert}) is not a readable PEM "
                                                      "certificate"))
            ok = False
        else:
            ends = got.not_valid_after_utc
            if ends < datetime.now(timezone.utc):
                out.append(Finding("timestamps", "warn", f"the TSA's certificate expired on {ends:%Y-%m-%d}"))
    pending = len(list((inst.requests / "sealed").glob("*.tsa_pending")))
    if pending:
        out.append(Finding("timestamps", "warn", f"{pending} lines wait for their timestamp: "
                                                 "`kapacitet seals timestamp`"))
    if ok:
        out.append(Finding("timestamps", "ok", f"TSA {parts.hostname}"))


def _check_telemetry(out: list[Finding]) -> None:
    asked = _env("KAPACITET_TELEMETRY").lower()
    endpoint = _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    if asked and asked not in ("off", "0", "false", "no", "console", "otlp"):
        out.append(Finding("telemetry", "warn", f"KAPACITET_TELEMETRY={asked} is not off, console or otlp"))
    if asked in ("off", "0", "false", "no") or (asked != "console" and not endpoint):
        out.append(Finding("telemetry", "ok", "off"))
        return
    if importlib.util.find_spec("opentelemetry") is None:
        out.append(Finding("telemetry", "error", "telemetry is on but opentelemetry is missing: install the "
                                                 "telemetry extra"))
        return
    if asked == "console":
        out.append(Finding("telemetry", "warn", "spans go to the server's log (KAPACITET_TELEMETRY=console), "
                                                "which is for development"))
        return
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        out.append(Finding("telemetry", "error", "OTEL_EXPORTER_OTLP_ENDPOINT is not an http(s) URL"))
        return
    if importlib.util.find_spec("opentelemetry.exporter.otlp.proto.http") is None:
        out.append(Finding("telemetry", "error", "the OTLP exporter is missing: install the telemetry extra"))
        return
    out.append(Finding("telemetry", "ok", f"OTLP to {parts.hostname}, service "
                                          f"{_env('OTEL_SERVICE_NAME') or 'kapacitet'}"))
    if parts.scheme == "http" and not _local_host(parts.hostname):
        out.append(Finding("telemetry", "warn", "the OTLP endpoint is not https"))


def _check_disk(inst: Instance, out: list[Finding]) -> None:
    where = inst.data if inst.data.exists() else inst.data.parent
    try:
        free = shutil.disk_usage(where).free
    except OSError:
        out.append(Finding("disk", "warn", f"cannot read the free space under {where}"))
        return
    text = f"{free / (1 << 30):.1f} GiB free under {where}"
    if free < DISK_ERROR_BYTES:
        out.append(Finding("disk", "error", text))
    elif free < DISK_WARN_BYTES:
        out.append(Finding("disk", "warn", text))
    else:
        out.append(Finding("disk", "ok", text))


def check(inst: Instance | None = None) -> list[Finding]:
    """Every finding about this instance, in the order of the report."""
    inst = inst or Instance.current()
    out: list[Finding] = []
    _check_data(inst, out)
    readable = _check_database(out)
    signin = _check_signin(out)
    _check_hosts(out, signin)
    _check_seals(inst, out, readable)
    _check_tsa(inst, out)
    _check_telemetry(out)
    _check_disk(inst, out)
    return out


def report(findings: list[Finding]) -> str:
    width = max((len(f.area) for f in findings), default=4)
    label = {"ok": "ok", "warn": "WARN", "error": "ERROR"}
    lines = [f"  {label[f.level]:<6} {f.area:<{width}}  {f.message}" for f in findings]
    errors = sum(f.level == "error" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    lines.append(f"{errors} error{'s' * (errors != 1)}, {warns} warning{'s' * (warns != 1)}")
    return "\n".join(lines)


def findings_json(findings: list[Finding]) -> str:
    return json.dumps({"ok": not any(f.level == "error" for f in findings),
                       "findings": [asdict(f) for f in findings]}, indent=1)


# ================================================================ backup

def _state_files(requests: Path) -> list[Path]:
    """The files of data/requests that are state: everything but the cache, the lock and temporaries."""
    if not requests.is_dir():
        return []
    out = []
    for path in sorted(requests.rglob("*")):
        rel = path.relative_to(requests)
        if rel.parts[0] in SKIP_TOP or not path.is_file() or _is_tmp(path):
            continue
        out.append(path)
    return out


def _empty_or_new(folder: Path, what: str) -> None:
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise InstanceError(f"{folder} is not empty: write the {what} into a new folder")


def _hold_requests(requests: Path, verb: str):
    """Hold data/requests/server.lock, as a running server does; refuse when one runs."""
    from . import procs
    from .api import records

    lock = requests / records.SERVER_LOCK
    try:
        pid = procs.hold(lock)
    except OSError as exc:
        raise InstanceError(f"cannot take {lock}: {exc}") from None
    if pid is not None:
        raise InstanceError(f"a Kapacitet+ server (PID {pid}) writes {requests}: stop it before you {verb} "
                            "the files backend (with PostgreSQL the backup runs beside the server)")
    return lock


def _copy_key(src: Path, dest: Path) -> dict:
    """The key folder's files into `dest` (the secret part of a backup): returns {path: sha256}."""
    from . import seals

    files = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file() or _is_tmp(path):
            continue
        rel = path.relative_to(src).as_posix()
        target = dest / rel
        write_bytes_atomic(target, path.read_bytes())
        seals._restrict(target)
        files[rel] = _sha256(target)
    return files


def backup(dest: Path, inst: Instance | None = None, key_out: Path | None = None,
           include_key: bool = True) -> dict:
    """Write a consistent backup of this instance into `dest` (empty or new); returns its manifest."""
    from . import __version__, store

    inst = inst or Instance.current()
    dest = Path(dest)
    _empty_or_new(dest, "backup")
    key_dest = Path(key_out) if key_out else dest / SECRET_DIR
    if include_key and key_out:
        _empty_or_new(key_dest, "seal key")
    keys, current, error = _keys_in(inst.keys)
    if include_key and error:
        raise InstanceError(error)
    if store.is_pg() and pg_tool("pg_dump") is None:
        raise InstanceError(_NO_PG_DUMP)
    manifest = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kapacitet_version": __version__, "backend": store.backend(), "scope": store.scope(),
                "files": {}, "seal_key": {"included": False, "key_id": current}}
    from . import procs

    # without a database, a running server is refused before anything is written
    lock = None if store.is_pg() else _hold_requests(inst.requests, "back up")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # the key first: its counts of signed lines then never exceed the chain the dump holds, which
        # a verification would take for signatures stripped
        if include_key and current:
            files = _copy_key(inst.keys, key_dest)
            write_text_atomic(key_dest / KEY_README, _SECRET_README)
            write_json_atomic(key_dest / KEY_MANIFEST, {"schema": SCHEMA, "secret": True, "key_id": current,
                                                         "retired": sorted(k for k in keys if k != current),
                                                         "files": files}, sort_keys=True)
            manifest["seal_key"] = {"included": True, "key_id": current,
                                    "folder": SECRET_DIR if not key_out else str(key_dest)}
        if store.is_pg():
            manifest["database"] = _pg_dump(dest / DUMP)
            manifest["files"][DUMP] = _sha256(dest / DUMP)
        else:
            for path in _state_files(inst.requests):
                rel = Path(REQUESTS) / path.relative_to(inst.requests)
                write_bytes_atomic(dest / rel, path.read_bytes())
                manifest["files"][rel.as_posix()] = _sha256(dest / rel)
    finally:
        if lock is not None:
            procs.let_go(lock)
    write_json_atomic(dest / MANIFEST, manifest, sort_keys=True)
    return manifest


_NO_PG_DUMP = ("pg_dump is not on the PATH: install the PostgreSQL client tools of the server's major version "
               "or later, or point KAPACITET_PG_BIN at them")


def _pg_dump(target: Path) -> dict:
    from . import store
    from .store import migrate

    url = store.database_url()
    tool = pg_tool("pg_dump")
    if tool is None:
        raise InstanceError(_NO_PG_DUMP)
    st = migrate.status(url)
    if st["pending"] or st["changed"]:
        raise InstanceError("the database's migrations are not all applied: run `kapacitet db migrate` first")
    conninfo, env = _pg_conninfo(url)
    done = subprocess.run([tool, "--format=custom", "--no-owner", "--no-privileges", f"--file={target}",
                           f"--dbname={conninfo}"], env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise InstanceError(f"pg_dump failed: {done.stderr.strip()[-500:]}")
    return {"url": describe_url(url),
            "migrations": [r["version"] for r in st["migrations"] if r["applied"]]}


# ================================================================ restore

def _read_manifest(src: Path) -> dict:
    try:
        manifest = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstanceError(f"{src} holds no readable {MANIFEST}: is it a Kapacitet+ instance backup?") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise InstanceError(f"{src / MANIFEST} is not a {SCHEMA} manifest")
    return manifest


def _verify_files(root: Path, files: dict) -> None:
    for rel, digest in files.items():
        path = root / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise InstanceError(f"the manifest names a path outside the backup: {rel}")
        if not path.is_file():
            raise InstanceError(f"{path} is missing from the backup")
        if _sha256(path) != digest:
            raise InstanceError(f"{path} does not match its SHA-256 in the manifest: the backup is damaged")


def _requests_empty(requests: Path) -> bool:
    if not requests.exists():
        return True
    return not any(p.name not in SKIP_TOP for p in requests.iterdir())


def _pg_empty() -> bool:
    from .store import db

    conn = db.connect()
    try:
        n = conn.execute("SELECT count(*) FROM information_schema.tables "
                         "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')").fetchone()[0]
    finally:
        conn.close()
    return n == 0


def restore(src: Path, inst: Instance | None = None, key_from: Path | None = None,
            include_key: bool = True) -> dict:
    """Put the backup in `src` into this instance, which must be empty; returns what was restored."""
    from . import seals, store

    inst = inst or Instance.current()
    src = Path(src)
    manifest = _read_manifest(src)
    key_src = Path(key_from) if key_from else src / SECRET_DIR
    key_manifest = None
    if include_key:
        try:
            key_manifest = json.loads((key_src / KEY_MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if manifest["seal_key"].get("key_id"):
                raise InstanceError(f"no seal key in {key_src}: pass --key-from with the backup's secret folder, "
                                    "or --no-key to restore without it (the chain's lines then verify only "
                                    "against its published key, and new seals start a new key)") from None
    # 1. everything is checked before anything is written
    _verify_files(src, manifest["files"])
    if key_manifest is not None:
        _verify_files(key_src, key_manifest.get("files", {}))
    if manifest["backend"] != store.backend():
        if manifest["backend"] == "postgres":
            raise InstanceError("the backup is of a PostgreSQL instance: set KAPACITET_DATABASE_URL to an empty "
                                "database and restore again")
        raise InstanceError("the backup is of the files backend and this instance uses PostgreSQL: restore it "
                            "without KAPACITET_DATABASE_URL, then `kapacitet db import-files`")
    if key_manifest is not None and (inst.keys / seals.KEY_FILE).exists():
        raise InstanceError(f"{inst.keys} already holds a signing key: restore into an instance without one")
    out = {"backend": manifest["backend"], "files": 0, "seal_key": None, "warnings": []}
    # 2. the state
    if store.is_pg():
        if not _pg_empty():
            raise InstanceError("the database is not empty: restore into a new database")
        _pg_restore(src / DUMP)
        from .store import migrate

        st = migrate.status()
        if st["pending"]:
            out["warnings"].append("the backup is from an older schema: run `kapacitet db migrate`")
        out["files"] = 1
    else:
        from . import procs

        if not _requests_empty(inst.requests):
            raise InstanceError(f"{inst.requests} already holds state: restore into an empty instance")
        lock = _hold_requests(inst.requests, "restore")
        try:
            if not _requests_empty(inst.requests):
                raise InstanceError(f"{inst.requests} already holds state: restore into an empty instance")
            for rel in manifest["files"]:
                parts = Path(rel).parts
                if parts[0] != REQUESTS:
                    continue
                write_bytes_atomic(inst.requests.joinpath(*parts[1:]), (src / rel).read_bytes())
                out["files"] += 1
        finally:
            procs.let_go(lock)
    # 3. the key: the private one never over another
    if key_manifest is not None:
        for rel in key_manifest["files"]:
            data = (key_src / rel).read_bytes()
            target = inst.keys / rel
            if rel == seals.KEY_FILE:
                if not seals._write_private(target, data, replace=False):
                    raise InstanceError(f"{target} appeared during the restore: another process made a key")
            else:
                write_bytes_atomic(target, data)
        out["seal_key"] = key_manifest.get("key_id")
    if manifest["backend"] == "postgres" and manifest.get("scope") != store.scope():
        out["warnings"].append(f"the backup's rows are under the scope {manifest.get('scope')} and this data folder "
                               f"is {store.scope()}: set KAPACITET_STORE_SCOPE={manifest.get('scope')}")
    # 4. the restored chain, verified with the restored keys
    keys, _current, _error = _keys_in(inst.keys)
    got = _verify_chain(inst, keys or None)
    out["chain"] = {"length": got["length"], "signed": got["signed"], "ok": got["ok"]}
    if not got["ok"]:
        out["warnings"].append(f"the restored chain does not verify (line {got.get('broken_at')}: "
                               f"{got.get('reason')})")
    return out


def _pg_restore(dump: Path) -> None:
    from . import store

    tool = pg_tool("pg_restore")
    if tool is None:
        raise InstanceError("pg_restore is not on the PATH: install the PostgreSQL client tools, or point "
                            "KAPACITET_PG_BIN at them")
    conninfo, env = _pg_conninfo(store.database_url())
    done = subprocess.run([tool, "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
                           f"--dbname={conninfo}", str(dump)], env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise InstanceError(f"pg_restore failed: {done.stderr.strip()[-500:]}")
