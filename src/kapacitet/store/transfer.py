"""Between the files backend and the database: `import_files` and `export_files`.

`import_files(root)` copies a data/requests/ folder into the database under the current scope.
It is one way and idempotent: a row already there is kept, so a second run adds nothing, and it
refuses to go on (TransferError) when the folder's seal chain and the database's disagree on a line
they both hold, since one of the two would then be lost. A settings document, a logo or a queue that the
database already holds with other content is kept and reported as a conflict, never overwritten.

`export_files(dest)` writes the database back in the same layout, into a folder that is empty or
new, for a backup or for `kapacitet seals verify` on the chain. Every text is kept as the files
backend writes it, so a folder imported and exported again comes back byte for byte, except what
the files themselves could not keep whole: a line cut short by a crash (a torn tail) and an index
line whose record had been removed are left out.

Not copied either way: data/requests/cache (the request's injection cache), the setup upload and
the server lock, which stay files whatever the backend.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from pathlib import Path

from ..atomic import write_bytes_atomic, write_text_atomic
from . import db, scope

_RECORD = re.compile(r"^([0-9a-f]{12})\.json\.gz$")
_QUEUE = re.compile(r"^queue(_mv\d{3})?$")


class TransferError(RuntimeError):
    pass


def _lines(path: Path) -> list[str]:
    """The whole lines of a JSON-lines file: a last line that does not parse (a torn tail) is left out."""
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except ValueError:
            if i == len(raw) - 1:
                continue
        out.append(line)
    return out


def _gzip(text: str) -> bytes:
    """The bytes atomic.gzip_json_atomic writes for this text (level 6, timestamp zero, no name)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def _queue_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and _QUEUE.match(p.name) and (p / "queue.json").is_file()) \
        if root.exists() else []


# ---------------------------------------------------------------- files -> database

def import_files(root: Path) -> dict:
    """Copy the folder `root` (data/requests) into the database; returns what was added."""
    root = Path(root)
    sc = scope()
    out = {"scope": sc, "records": 0, "index_without_record": 0, "applications": 0, "market_alternatives": 0,
           "settings": "absent", "brand_logo": "absent", "seal_lines": 0, "sealed_copies": 0, "fingerprints": 0, "timestamps": 0, "queues": {},
           "conflicts": []}
    with db.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{sc}:import",))
        _import_records(conn, root, sc, out)
        for i, line in enumerate(_lines(root / "applications.jsonl"), start=1):
            try:
                seq = int(json.loads(line).get("seq", i))
            except (ValueError, TypeError, AttributeError):
                seq = i
            got = conn.execute("SELECT line FROM applications_log WHERE scope = %s AND seq = %s", (sc, seq)).fetchone()
            if got is None:
                conn.execute("INSERT INTO applications_log (scope, seq, line) VALUES (%s, %s, %s)", (sc, seq, line))
                out["applications"] += 1
            elif got[0] != line:
                out["conflicts"].append(f"applications.jsonl line {seq}")
        for i, line in enumerate(_lines(root / "market_alternatives.jsonl"), start=1):
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cur = conn.execute("INSERT INTO market_alternatives (scope, seq, id, line) VALUES (%s, %s, %s, %s)"
                               " ON CONFLICT DO NOTHING", (sc, i, item["id"], line))
            out["market_alternatives"] += cur.rowcount
        settings = root / "settings.json"
        if settings.is_file():
            text = settings.read_text(encoding="utf-8")
            got = conn.execute("SELECT doc FROM dso_settings WHERE scope = %s", (sc,)).fetchone()
            if got is None:
                conn.execute("INSERT INTO dso_settings (scope, doc) VALUES (%s, %s)", (sc, text))
                out["settings"] = "imported"
            elif got[0] == text:
                out["settings"] = "same"
            else:
                out["settings"] = "kept"
                out["conflicts"].append("settings.json")
        logo = root / "brand-logo.png"
        if logo.is_file():
            data = logo.read_bytes()
            got = conn.execute("SELECT png FROM brand_logo WHERE scope = %s", (sc,)).fetchone()
            if got is None:
                conn.execute("INSERT INTO brand_logo (scope, png) VALUES (%s, %s)", (sc, data))
                out["brand_logo"] = "imported"
            elif bytes(got[0]) == data:
                out["brand_logo"] = "same"
            else:
                out["brand_logo"] = "kept"
                out["conflicts"].append("brand-logo.png")
        _import_chain(conn, root, sc, out)
        for folder in _queue_dirs(root):
            out["queues"][folder.name] = _import_queue(conn, folder, sc, out)
    _carry_marks(root, sc)
    return out


def _import_records(conn, root: Path, sc: str, out: dict) -> None:
    index: dict[str, str] = {}
    order: list[str] = []
    for line in _lines(root / "index.jsonl"):
        item = json.loads(line)
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            if item["id"] not in index:
                order.append(item["id"])
            index[item["id"]] = line
    stored = {m[1]: p for p in sorted(root.glob("*.json.gz")) if (m := _RECORD.match(p.name))} if root.exists() else {}
    out["index_without_record"] = sum(1 for k in index if k not in stored)
    for job_id in [k for k in order if k in stored] + [k for k in stored if k not in index]:
        with gzip.open(stored[job_id], "rt", encoding="utf-8") as fh:
            text = fh.read()
        created = json.loads(text).get("created")
        cur = conn.execute("INSERT INTO assessments (scope, id, created, doc, line) VALUES (%s, %s, %s, %s, %s)"
                           " ON CONFLICT DO NOTHING", (sc, job_id, created, text, index.get(job_id)))
        out["records"] += cur.rowcount


def _import_chain(conn, root: Path, sc: str, out: dict) -> None:
    from .postgres import _CHAIN_COLUMNS

    lines = _lines(root / "seals.jsonl")
    have = [r[0] for r in conn.execute("SELECT line FROM seal_chain WHERE scope = %s ORDER BY seq", (sc,)).fetchall()]
    for i, (a, b) in enumerate(zip(have, lines), start=1):
        if a != b:
            raise TransferError(f"the seal chain in {root} and the database's ({sc}) differ at line {i}: "
                                "neither is imported over the other")
    if len(have) > len(lines):
        raise TransferError(f"the database's seal chain ({sc}) is longer than the one in {root}: nothing to import "
                            "into it from an older copy")
    for line in lines[len(have):]:
        entry = json.loads(line)
        conn.execute("INSERT INTO seal_chain (scope, " + ", ".join(_CHAIN_COLUMNS) + ", line) VALUES ("
                     + ", ".join(["%s"] * (len(_CHAIN_COLUMNS) + 2)) + ")",
                     (sc, *[entry.get(k) for k in _CHAIN_COLUMNS], line))
        out["seal_lines"] += 1
    sealed = root / "sealed"
    if sealed.is_dir():
        for kind_dir in sorted(p for p in sealed.iterdir() if p.is_dir()):
            for path in sorted(kind_dir.glob("*.json")):
                if kind_dir.name == "fingerprints":
                    cur = conn.execute("INSERT INTO seal_fingerprints (scope, digest, body) VALUES (%s, %s, %s)"
                                       " ON CONFLICT DO NOTHING", (sc, path.stem, path.read_text(encoding="utf-8")))
                    out["fingerprints"] += cur.rowcount
                else:
                    cur = conn.execute("INSERT INTO sealed_copies (scope, kind, id, body) VALUES (%s, %s, %s, %s)"
                                       " ON CONFLICT DO NOTHING", (sc, kind_dir.name, path.stem, path.read_bytes()))
                    out["sealed_copies"] += cur.rowcount
        # the RFC 3161 tokens and the markers of those still owed (seals.FolderChain)
        from ..seals import PENDING_SUFFIX, TOKEN_SUFFIX

        for suffix, column in ((TOKEN_SUFFIX, "token"), (PENDING_SUFFIX, "pending")):
            for path in sorted(sealed.glob(f"*{suffix}")):
                seq = path.name[:-len(suffix)]
                if not seq.isdigit() or not path.is_file():
                    continue
                body = path.read_bytes() if column == "token" else path.read_bytes().decode("utf-8")
                cur = conn.execute(f"INSERT INTO seal_timestamps (scope, seq, {column}) VALUES (%s, %s, %s)"
                                   " ON CONFLICT DO NOTHING", (sc, int(seq), body))
                out["timestamps"] += cur.rowcount
    pub = root / "seal_key.pub"
    if pub.is_file():
        conn.execute("INSERT INTO store_meta (scope, key, value) VALUES (%s, 'seal_key.pub', %s) ON CONFLICT DO NOTHING",
                     (sc, pub.read_text(encoding="ascii")))


def _import_queue(conn, folder: Path, sc: str, out: dict) -> str:
    text = (folder / "queue.json").read_text(encoding="utf-8")
    marker = folder / "seal_pending.json"
    got = conn.execute("SELECT doc FROM queues WHERE scope = %s AND name = %s", (sc, folder.name)).fetchone()
    if got is not None and got[0] is not None and got[0] != text:
        out["conflicts"].append(f"{folder.name}/queue.json")
        return "kept"
    q = json.loads(text)
    conn.execute("INSERT INTO queues (scope, name, version, baseline, doc, seal_pending) VALUES (%s, %s, %s, %s, %s, %s)"
                 " ON CONFLICT (scope, name) DO UPDATE SET version = EXCLUDED.version, baseline = EXCLUDED.baseline,"
                 " doc = EXCLUDED.doc, seal_pending = coalesce(queues.seal_pending, EXCLUDED.seal_pending)"
                 " WHERE queues.doc IS NULL",
                 (sc, folder.name, int(q.get("version", 0)), q.get("baseline"), text,
                  marker.read_text(encoding="utf-8") if marker.is_file() else None))
    for path in sorted(folder.glob("*.npz")):
        conn.execute("INSERT INTO queue_files (scope, name, file, body) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                     (sc, folder.name, path.name, path.read_bytes()))
    return "same" if got is not None and got[0] == text else "imported"


def _carry_marks(root: Path, sc: str) -> None:
    """The count of signed lines this installation has seen in the folder's chain carries over to
    the database's, so a later rewrite that strips signatures there is still caught."""
    from .. import seals
    from ..atomic import write_json_atomic

    count = sum(1 for line in _lines(root / "seals.jsonl") if json.loads(line).get("sig"))
    if not count:
        return
    marks = seals._read_marks()
    name = f"postgres:{sc}"
    if count > marks.get(name, 0):
        marks[name] = count
        try:
            write_json_atomic(seals._marks_file(), marks, sort_keys=True)
        except OSError:
            pass


# ---------------------------------------------------------------- database -> files

def export_files(dest: Path) -> dict:
    """Write the database's state for the current scope into `dest` (empty or new), in the files
    backend's layout; returns what was written."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise TransferError(f"{dest} is not empty: export into a new folder")
    dest.mkdir(parents=True, exist_ok=True)
    sc = scope()
    out = {"scope": sc, "records": 0, "applications": 0, "market_alternatives": 0, "settings": False,
           "brand_logo": False, "seal_lines": 0, "sealed_copies": 0, "fingerprints": 0, "timestamps": 0, "queues": []}
    with db.connection() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        index = []
        for job_id, doc, line in conn.execute("SELECT id, doc, line FROM assessments WHERE scope = %s ORDER BY pos",
                                              (sc,)).fetchall():
            write_bytes_atomic(dest / f"{job_id}.json.gz", _gzip(doc))
            out["records"] += 1
            if line is not None:
                index.append(line)
        if index:
            write_text_atomic(dest / "index.jsonl", "".join(x + "\n" for x in index))
        for table, name, key in (("applications_log", "applications.jsonl", "applications"),
                                 ("market_alternatives", "market_alternatives.jsonl", "market_alternatives")):
            rows = [r[0] for r in conn.execute(f"SELECT line FROM {table} WHERE scope = %s ORDER BY seq", (sc,))]
            if rows:
                write_text_atomic(dest / name, "".join(x + "\n" for x in rows))
            out[key] = len(rows)
        got = conn.execute("SELECT doc FROM dso_settings WHERE scope = %s", (sc,)).fetchone()
        if got:
            write_text_atomic(dest / "settings.json", got[0])
            out["settings"] = True
        got = conn.execute("SELECT png FROM brand_logo WHERE scope = %s", (sc,)).fetchone()
        if got:
            write_bytes_atomic(dest / "brand-logo.png", bytes(got[0]))
            out["brand_logo"] = True
        chain = [r[0] for r in conn.execute("SELECT line FROM seal_chain WHERE scope = %s ORDER BY seq", (sc,))]
        if chain:
            write_text_atomic(dest / "seals.jsonl", "".join(x + "\n" for x in chain))
        out["seal_lines"] = len(chain)
        for kind, item_id, body in conn.execute("SELECT kind, id, body FROM sealed_copies WHERE scope = %s", (sc,)):
            write_bytes_atomic(dest / "sealed" / kind / f"{item_id}.json", bytes(body))
            out["sealed_copies"] += 1
        for digest, body in conn.execute("SELECT digest, body FROM seal_fingerprints WHERE scope = %s", (sc,)):
            write_text_atomic(dest / "sealed" / "fingerprints" / f"{digest}.json", body)
            out["fingerprints"] += 1
        from ..seals import PENDING_SUFFIX, TOKEN_SUFFIX

        for seq, token, pending in conn.execute("SELECT seq, token, pending FROM seal_timestamps WHERE scope = %s",
                                                (sc,)):
            if token is not None:
                write_bytes_atomic(dest / "sealed" / f"{seq}{TOKEN_SUFFIX}", bytes(token))
            else:
                write_text_atomic(dest / "sealed" / f"{seq}{PENDING_SUFFIX}", pending)
            out["timestamps"] += 1
        got = conn.execute("SELECT value FROM store_meta WHERE scope = %s AND key = 'seal_key.pub'", (sc,)).fetchone()
        if got:
            write_text_atomic(dest / "seal_key.pub", got[0], encoding="ascii")
        for name, doc, marker in conn.execute("SELECT name, doc, seal_pending FROM queues WHERE scope = %s"
                                              " AND doc IS NOT NULL AND name NOT LIKE '%%.next' ORDER BY name", (sc,)):
            folder = dest / name
            write_text_atomic(folder / "queue.json", doc)
            if marker is not None:
                write_text_atomic(folder / "seal_pending.json", marker)
            for file, body in conn.execute("SELECT file, body FROM queue_files WHERE scope = %s AND name = %s",
                                           (sc, name)):
                write_bytes_atomic(folder / file, bytes(body))
            out["queues"].append(name)
    return out
