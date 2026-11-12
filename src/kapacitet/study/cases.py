"""Module 2 of a study: a batch of connection cases checked station by station, as files.

    kapacitet study cases <dir> <cases.csv> [--batch NAME] [--compare-decisions]

The input has one row per case: case_id, received_at, connection_point_id, pv_kwp, pv_kw_ac,
pv_phases, ev_kw, ev_phases, hp, bat_kwh, and optionally decision_dso, what the DSO decided
(`--compare-decisions` sets the two side by side). A connection point is a customer_id of the
station contracts, found with DuckDB over every station's processed/grid/customers.parquet. The
cases are grouped by station, and each station's are checked by a process of its own,
`kapacitet station cases <sid> --study <dir> --batch <name>`, with KAPACITET_DATA_DIR on its
folder, as a build is (study/portfolio.py): it reads the batch's work/<sid>.json and writes only
its own folder, the assessments in requests/ (sealed in the station's chain) and what it found in
cases/<batch>.json.

Each case is checked with engine/request.check across the station's MV band (generation at both
ends, the lower one counts; consumption at the low end), against the "existing" baseline the
station's build precomputed on the same model at each end, or against the station's queue at that
end when `study sync` has built one. The proposal is engine/rules.verdict's: firm, nonfirm or
review, never a refusal; Kapacitet+ proposes and a person decides.

The batch writes, in cases/<batch>/:

    verdicts.csv (+ .machine.csv)   one row per case: the proposal, its reasons as codes and in
                                    Swedish, the curtailment on average and in one year in ten,
                                    fuse and phases, the assessment id and its seal's digest, the
                                    method version and the station's fingerprint; a case that
                                    cannot be checked is a row with its error, never a crash
    annex/<case>.md and .json       the case's annex, in Swedish
    drafts/<case>/*.md              the drafts a handler may send (templates/cases_sv): the time
                                    plan always, the three-month status when a person has to decide,
                                    and a reinforcement proposal when the static study rejects a
                                    part, its next transformer verified on NetModel(trafo_type=...)
    confusion.csv                   with --compare-decisions: proposals against decision_dso

A case is idempotent on its case_id: the assessment's id is a digest of the case, its request, the
station's fingerprint and the queue it met, so running a batch again reuses each assessment
instead of computing and sealing a second one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import paths, procs
from ..atomic import write_json_atomic, write_text_atomic
from . import RESERVED, Study, StudyError, valid_id
from .config import METHOD_VERSION
from .csvio import number, read
from .csvio import write as write_csv

log = logging.getLogger("kapacitet.study.cases")

REQUIRED = ("case_id", "received_at", "connection_point_id")
NUMBERS = ("pv_kwp", "pv_kw_ac", "ev_kw", "bat_kwh")
PHASES = ("pv_phases", "ev_phases")
COLUMNS = REQUIRED + ("pv_kwp", "pv_kw_ac", "pv_phases", "ev_kw", "ev_phases", "hp", "bat_kwh")
OPTIONAL = ("decision_dso",)
CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
PROPOSALS = ("firm", "nonfirm", "review")
VERDICT_COLUMNS = ["case_id", "received_at", "station", "connection_point_id", "proposal", "reason_codes",
                   "reason_sv", "curtailment_mean_pct", "curtailment_p90_pct", "fuse_ok", "phases_ok",
                   "binding_mv_pu", "rule", "assessment_id", "seal_digest", "method_version", "fingerprint",
                   "decision_dso", "error", "error_text"]
TEMPLATES = Path(__file__).with_name("templates") / "cases_sv"
DRAFTS = ("tidsplan", "status_3m", "forstarkning")
PROPOSAL_SV = {"firm": "fast anslutning",
               "nonfirm": "villkorad anslutning med dynamisk exportgräns",
               "review": "prövas av en handläggare"}
PROTECTION_SV = "kontrolleras i dpPower, utanför Kapacitet+ ([cases] protection = \"dppower\")."

# A row the batch cannot check: its code, and the sentence in the row's error_text.
ROW_ERRORS = {
    "bad_case_id": "case_id {value!r} cannot name a file (letters, digits, - _ and ., at most 64)",
    "duplicate_case": "case_id {value} appears more than once in the file",
    "bad_value": "{column} = {value!r} cannot be read",
    "bad_phases": "{column} = {value!r}: phases are 1 or 3, or blank for the usual",
    "nothing_requested": "the case asks for no solar, charger or heat pump",
    "battery_needs_solar": "a battery is checked only with solar",
    "unknown_point": "connection point {value} is in none of the study's stations",
    "ambiguous_point": "connection point {value} is in more than one station: {stations}",
    "station_not_ready": "station {station} is not built (its status is {status})",
    "station_stale": "station {station} was built without the request baselines of its MV band: build it again",
    "has_heat_pump": "the house already has a heat pump",
    "already_approved": "the house is already approved in the station's queue",
    "house_twice": "the house is already in this list, as case {other}",
    "bad_decision": "decision = {value!r}: an approved connection is firm or nonfirm",
    "station_failed": "the station's check failed: {detail}",
}


class CaseError(ValueError):
    """A case that cannot be checked: a row with its error, never a crash."""

    def __init__(self, code: str, **params):
        super().__init__(ROW_ERRORS[code].format(**params))
        self.code = code
        self.params = params


def row_error(case_id: str, code: str, station: str = "", **params) -> dict:
    return {"case_id": case_id, "station": station, "error": code,
            "error_text": ROW_ERRORS[code].format(**params)}


CAUSE_LENGTH = 600


def _middle_cut(text: str, limit: int = CAUSE_LENGTH) -> str:
    """`text` on one line and within `limit`, keeping its start and its end: an error about a file
    ends with the file's name, which a cut at the end is what lost."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    keep = (limit - 5) // 2
    return f"{text[:keep]} ... {text[-keep:]}"


def cause(exc: BaseException) -> str:
    """What failed, for a row of the errors file: the error's kind and its own text."""
    text = _middle_cut(str(exc))
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def process_failure(study: Study, sid: str, doc: dict) -> str:
    """Why a station's process gave no result: how it ended, the last line it wrote (a Python
    process ends its traceback with the error itself) and where the rest is."""
    code = doc["failed"]
    how = "the station's process did not finish" if code is None else f"the station's process ended with {code}"
    last = next((ln.strip() for ln in reversed(doc.get("log") or []) if ln.strip()), "")
    where = f"see {study.station_dir(sid) / procs.LOG}"
    return f"{how}: {_middle_cut(last)} ({where})" if last else f"{how} ({where})"


# ---------------------------------------------------------------- reading the cases

def _truth(column: str, text: str) -> bool:
    t = (text or "").strip().lower()
    if t in ("", "0", "false", "nej", "no", "n"):
        return False
    if t in ("1", "true", "ja", "yes", "y", "j", "x"):
        return True
    raise CaseError("bad_value", column=column, value=text)


def _when(column: str, text: str) -> str:
    """An ISO 8601 date or time, as given (its date is what the drafts use)."""
    try:
        datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise CaseError("bad_value", column=column, value=text) from None
    return text.strip()


def parse_row(row: dict, extra: tuple[str, ...] = ()) -> dict:
    """One case from its CSV row: the request in the engine's terms, or a CaseError."""
    case_id = row.get("case_id", "")
    if not CASE_ID.match(case_id) or case_id.split(".")[0].upper() in RESERVED:
        raise CaseError("bad_case_id", value=case_id)
    req: dict = {}
    for col in NUMBERS:
        try:
            v = number(row.get(col))
        except ValueError:
            raise CaseError("bad_value", column=col, value=row.get(col)) from None
        if v is not None and (v < 0 or v != v):
            raise CaseError("bad_value", column=col, value=row.get(col))
        req[col] = float(v or 0.0)
    for col in PHASES:
        text = (row.get(col) or "").strip()
        try:
            v = int(number(text) or 0)
        except ValueError:
            raise CaseError("bad_value", column=col, value=text) from None
        if v not in (0, 1, 3):
            raise CaseError("bad_phases", column=col, value=text)
        req[col] = v
    req["hp"] = _truth("hp", row.get("hp", ""))
    if req["pv_kwp"] <= 0 and req["ev_kw"] <= 0 and not req["hp"]:
        raise CaseError("nothing_requested")
    if req["bat_kwh"] > 0 and req["pv_kwp"] <= 0:
        raise CaseError("battery_needs_solar")
    point = (row.get("connection_point_id") or "").strip()
    if not point:
        raise CaseError("bad_value", column="connection_point_id", value=point)
    case = {"case_id": case_id, "received_at": _when("received_at", row.get("received_at", "")),
            "connection_point_id": point, "request": req,
            "decision_dso": (row.get("decision_dso") or "").strip().lower() or None}
    for col in extra:
        case[col] = (row.get(col) or "").strip()
    return case


def read_cases(path: Path, extra: tuple[str, ...] = ()) -> tuple[list[dict], list[dict]]:
    """The cases of a CSV (either dialect) and the rows that cannot be read. A missing column is
    the file's error, refused before anything runs."""
    path = Path(path)
    if not path.is_file():
        raise StudyError("no_cases", path=str(path))
    rows = read(path)
    have = set(rows[0]) if rows else set()
    missing = [c for c in (*COLUMNS, *extra) if c not in have]
    if rows and missing:
        raise StudyError("cases_columns", path=str(path), columns=", ".join(missing))
    cases, errors, seen = [], [], set()
    for row in rows:
        cid = row.get("case_id", "")
        given = {"received_at": row.get("received_at") or None, "connection_point_id": row.get("connection_point_id") or None}
        if cid and cid in seen:
            errors.append({**row_error(cid, "duplicate_case", value=cid), **given})
            continue
        seen.add(cid)
        try:
            cases.append(parse_row(row, extra))
        except CaseError as err:
            errors.append({**row_error(cid, err.code, **err.params), **given})
    return cases, errors


# ---------------------------------------------------------------- where each case is

def locate(study: Study, points: list[str]) -> dict[str, list[tuple[str, int]]]:
    """Each connection point's (station, cid) in every station that has it, from the stations'
    customers.parquet (its customer_id is the column `osm_id`), with DuckDB."""
    import duckdb

    files = sorted(study.stations_dir.glob("*/processed/grid/customers.parquet"))
    if not files or not points:
        return {}
    con = duckdb.connect()
    try:
        con.execute("CREATE TEMP TABLE wanted (point VARCHAR)")
        con.executemany("INSERT INTO wanted VALUES (?)", [(p,) for p in sorted(set(points))])
        rows = con.execute(
            "SELECT c.osm_id, c.cid, c.filename FROM read_parquet(?, filename = true) c "
            "JOIN wanted w ON CAST(c.osm_id AS VARCHAR) = w.point ORDER BY c.filename, c.cid",
            [[f.as_posix() for f in files]]).fetchall()
    finally:
        con.close()
    out: dict[str, list[tuple[str, int]]] = {}
    for point, cid, filename in rows:
        sid = Path(filename).parents[2].name
        if valid_id(sid):
            out.setdefault(str(point), []).append((sid, int(cid)))
    return out


def _ready(study: Study, sid: str) -> str | None:
    """None when the station can check cases, otherwise its status."""
    if not study.has(sid):
        return "unknown"
    status = study.record(sid).get("status")
    return None if status == "ready" else str(status)


def group(study: Study, cases: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """The cases by station, each with its cid, and the rows no station can take."""
    where = locate(study, [c["connection_point_id"] for c in cases])
    by_station: dict[str, list[dict]] = {}
    errors = []
    for c in cases:
        found = where.get(c["connection_point_id"], [])
        if not found:
            errors.append(row_error(c["case_id"], "unknown_point", value=c["connection_point_id"]))
            continue
        if len({s for s, _ in found}) > 1 or len(found) > 1:
            errors.append(row_error(c["case_id"], "ambiguous_point", value=c["connection_point_id"],
                                    stations=", ".join(sorted({s for s, _ in found}))))
            continue
        sid, cid = found[0]
        status = _ready(study, sid)
        if status is not None:
            errors.append(row_error(c["case_id"], "station_not_ready", station=sid, status=status))
            continue
        by_station.setdefault(sid, []).append({**c, "cid": cid, "station": sid})
    return by_station, errors


# ---------------------------------------------------------------- the station processes

def batch_dir(study: Study, batch: str) -> Path:
    if not valid_id(batch):
        raise StudyError("bad_batch", batch=batch)
    return study.root / "cases" / batch


def station_output(batch: str, kind: str = "cases") -> Path:
    """What a station's process writes for a batch, in its own folder (KAPACITET_DATA_DIR)."""
    return paths.DATA / kind / f"{batch}.json"


def spawn(study: Study, sid: str, action: str, batch: str, allow_git: bool = False,
          timeout: float | None = None) -> int | None:
    """`kapacitet station <action> <sid> --study <dir> --batch <batch>` with the station's
    environment (portfolio.env_for), its output in the station's build.log."""
    from .portfolio import env_for

    cmd = [sys.executable, "-m", "kapacitet", "station", action, sid, "--study", str(study.root), "--batch", batch]
    if allow_git:
        cmd.append("--allow-git")
    folder = study.station_dir(sid)
    with open(folder / procs.LOG, "ab") as fh:
        fh.write(f"\n=== {procs.now()} {' '.join(cmd)}\n".encode("utf-8"))
        fh.flush()
        try:
            return subprocess.run(cmd, env=env_for(study, sid), cwd=str(paths.ROOT), stdout=fh,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return None


Runner = Callable[[Study, str, str, str], "int | None"]


def run_stations(study: Study, action: str, batch: str, work: dict[str, dict], jobs: int | None = None,
                 runner: Runner | None = None, allow_git: bool = False, work_dir: Path | None = None) -> dict[str, dict]:
    """Write each station's work file and run its process (at most `jobs` at a time); returns what
    each wrote, or {"failed": code} for a station whose process did not finish. `runner` stands in
    for the process in a test: runner(study, sid, action, batch) -> exit code. The work files go
    in cases/<batch>/work, or in `work_dir`."""
    import os

    from .portfolio import MAX_JOBS

    folder = work_dir or batch_dir(study, batch) / "work"
    for sid, doc in work.items():
        write_json_atomic(folder / f"{sid}.json", doc)
        (study.station_dir(sid) / action / f"{batch}.json").unlink(missing_ok=True)
    run = runner or (lambda st, sid, act, b: spawn(st, sid, act, b, allow_git=allow_git))
    n = max(1, min(jobs or MAX_JOBS, os.cpu_count() or 1, len(work) or 1))

    def one(sid: str) -> dict:
        code = run(study, sid, action, batch)
        out = study.station_dir(sid) / action / f"{batch}.json"
        if code == 0 and out.is_file():
            return json.loads(out.read_text(encoding="utf-8"))
        return {"failed": code, "log": procs.log_tail(study.station_dir(sid), 5)}

    with ThreadPoolExecutor(max_workers=n) as ex:
        return dict(zip(work, ex.map(one, list(work))))


# ---------------------------------------------------------------- inside a station's process

def _baselines_ready(band: tuple[float, float]) -> bool:
    from ..engine import request as rq
    from ..paths import processed

    return all(processed("request", f"{rq.band_name('existing', e)}.json").is_file() for e in sorted(set(band)))


def station_context() -> dict:
    """What every case of this station is checked with: the band and fingerprint of its build, the
    masks, the DSO's thresholds and the queue at each end of the band (if `study sync` built one)."""
    from .. import dso
    from ..engine import queue as qu
    from ..engine import request as rq
    from ..engine.run import background_masks
    from .station import capacity_path

    cap = json.loads(capacity_path().read_text(encoding="utf-8"))
    band = (float(min(cap["mv_band_pu"])), float(max(cap["mv_band_pu"])))
    ends = sorted(set(band))
    hp, ev = background_masks(rq.model_at(band[0]).C)
    contexts = {}
    for e in ends:
        ctx = qu.context(qu.band_folder(e))
        if ctx is not None:
            contexts[e] = ctx
    return {"band": band, "ends": ends, "fingerprint": cap.get("fingerprint"), "hp": hp, "ev": ev,
            "thresholds": dso.thresholds(), "contexts": contexts,
            "queues": {f"{e:.2f}": c.label for e, c in contexts.items()},
            "ready": _baselines_ready(band)}


def assessment_id(case: dict, sctx: dict) -> str:
    """A case's assessment: the same case, request, station build and queue give the same id."""
    key = {"case_id": case["case_id"], "request": case["request"], "cid": case["cid"],
           "fingerprint": sctx["fingerprint"], "queues": sctx["queues"], "method_version": METHOD_VERSION}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _engine_request(case: dict, baseline: str):
    from ..engine import queue as qu

    return qu.engine_request({**case["request"], "cid": case["cid"]}, baseline)


def _brief(result: dict) -> dict:
    """An end's result as the record keeps it beside the combined one: no hourly profiles."""
    risk = {k: v for k, v in (result.get("risk") or {}).items() if k != "histogram"}
    return {"static": result.get("static"), "fuse": result.get("fuse"), "phases": result.get("phases"),
            "verification": result.get("verification"), "risk": risk, "summary": result.get("summary")}


def assess(case: dict, sctx: dict) -> dict:
    """The case's saved assessment: reused when it is already kept, otherwise checked across the
    band, saved and sealed in the station's chain."""
    from ..api import records
    from ..engine import request as rq

    aid = assessment_id(case, sctx)
    rec = records.load(aid, resolve=False)
    if rec is not None:
        return rec
    hp, ev = sctx["hp"], sctx["ev"]
    if case["request"]["hp"] and bool(hp[case["cid"]]):
        raise CaseError("has_heat_pump")
    if any(case["cid"] in c.overrides for c in sctx["contexts"].values()):
        raise CaseError("already_approved")
    baseline = "queue" if sctx["contexts"] else "existing"
    req = _engine_request(case, baseline)
    out = rq.check(req, hp, ev, sctx["band"], contexts=sctx["contexts"])
    result = {**out["result"], "mv_ends": {k: _brief(v) for k, v in out["by_end"].items()}}
    request = {"case_id": case["case_id"], "connection_point_id": case["connection_point_id"],
               "received_at": case["received_at"], "cid": case["cid"], **case["request"], "baseline": baseline}
    provenance = {"station": case["station"], "fingerprint": sctx["fingerprint"], "method_version": METHOD_VERSION,
                  "mv_band_pu": list(sctx["band"]), "baselines": [rq.band_name("existing", e) for e in sctx["ends"]],
                  "queues": sctx["queues"]}
    rec = records.make_record(aid, time.time(), request, result, provenance)
    records.save(rec)
    return rec


def _static_parts(result: dict) -> dict:
    return {part: {"approved": bool(v.get("approved")), "reason_code": v.get("reason_code"),
                   "reason_value": v.get("reason_value")} for part, v in (result.get("static") or {}).items()}


def next_transformer(net) -> dict | None:
    """The smallest of pandapower's standard transformers for the same voltages that is larger than
    the station's, or None when there is none."""
    import pandapower as pp

    t = net.trafo.iloc[0]
    try:
        types = pp.available_std_types(net, element="trafo")
    except Exception:  # noqa: BLE001 - a network without the standard library: nothing to propose
        return None
    same = types[(abs(types["vn_hv_kv"] - float(t["vn_hv_kv"])) < 1e-6)
                 & (abs(types["vn_lv_kv"] - float(t["vn_lv_kv"])) < 1e-6)
                 & (types["sn_mva"] > float(t["sn_mva"]) + 1e-9)].sort_values("sn_mva")
    if same.empty:
        return None
    return {"std_type": str(same.index[0]), "sn_mva": float(same.iloc[0]["sn_mva"])}


def reinforcement(case: dict, sctx: dict) -> dict:
    """The next transformer, verified: the static study run again for the case on
    NetModel(trafo_type=next) at each end of the band it is checked at (generation at both,
    consumption at the low end), against the same grid. `approved` says whether that is enough;
    when it is not, the limit is not the transformer alone."""
    from ..engine import request as rq

    m = rq.model_at(sctx["band"][0])
    t = m.net.trafo.iloc[0]
    now = {"std_type": None if t.get("std_type") is None else str(t["std_type"]), "sn_mva": float(t["sn_mva"])}
    nxt = next_transformer(m.net)
    if nxt is None:
        return {"current": now, "next": None, "approved": False, "by_end": {}}
    hp, ev = sctx["hp"], sctx["ev"]
    req = _engine_request(case, "existing")
    low, high = sctx["band"]
    ends = sorted({low, high}) if req.pv_kwp > 0 else [low]
    by_end = {}
    for e in ends:
        r = req if e == low else rq._generation_only(req)
        ctx = sctx["contexts"].get(e) or rq.Context(rq.band_name("existing", e))
        base = rq._baseline(ctx.name)
        hp_new = bool(r.hp) and not bool(hp[r.cid])
        static = rq._static(rq.model_at(e, trafo_type=nxt["std_type"]), r, base, hp, ev, ctx, hp_new)
        by_end[f"{e:.2f}"] = _static_parts({"static": static})
    approved = all(p["approved"] for parts in by_end.values() for p in parts.values())
    return {"current": now, "next": nxt, "approved": approved, "by_end": by_end}


def _protection_rows(mode: str) -> dict[int, dict] | str:
    """The house rows of protection.assess, by cid, when the study asks Kapacitet+ for them; the
    reason they could not be computed otherwise."""
    if mode != "kapacitet":
        return {}
    try:
        from ..engine import protection
        from ..grid.builder import load_net, load_tables

        net = load_net()
        _, lines, cust = load_tables()
        return {h["cid"]: h for h in protection.assess(net, lines, cust)["houses"]}
    except Exception as exc:  # noqa: BLE001 - said in the annex, never a crash
        return f"{type(exc).__name__}: {exc}"


def read_signature(study: Study) -> dict | None:
    path = study.root / "rule_signature.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def check_one(study: Study, case: dict, sctx: dict, protection: dict | str, signature: dict | None) -> dict:
    """One case checked: its proposal and everything its row, annex and drafts say."""
    from .. import seals
    from ..engine import rules

    rec = assess(case, sctx)
    res = rec["result"]
    v = rules.verdict(res, sctx["thresholds"])
    entry = seals.entry_for("assessment", rec["id"])
    risk = res.get("risk") or {}
    static = _static_parts(res)
    out = {"case_id": case["case_id"], "station": case["station"], "cid": case["cid"],
           "connection_point_id": case["connection_point_id"], "received_at": case["received_at"],
           "request": case["request"], "decision_dso": case.get("decision_dso"),
           "assessment_id": rec["id"], "assessed_at": rec["created"], "seal_digest": entry["digest"] if entry else None,
           "proposal": v["outcome"], "reasons": v["reasons"], "static": static,
           "curtailment_mean_pct": risk.get("mean_pct"), "curtailment_p90_pct": risk.get("p90_pct"),
           "fuse_ok": (res.get("fuse") or {}).get("ok"), "phases_ok": (res.get("phases") or {}).get("ok"),
           "fuse": res.get("fuse"), "phases": res.get("phases"),
           "binding_mv_pu": res.get("binding_mv_pu"), "mv_band_pu": res.get("mv_band_pu"),
           "summary": res.get("summary"), "queue": res.get("queue"),
           "rule": rules.scope(signature, case["request"], sctx["thresholds"], date.today()) or "signed",
           "fingerprint": sctx["fingerprint"], "method_version": METHOD_VERSION,
           "protection_mode": study.config.cases.protection}
    if study.config.cases.protection == "kapacitet":
        out["protection"] = protection.get(case["cid"]) if isinstance(protection, dict) else {"error": protection}
    if static and not all(p["approved"] for p in static.values()):
        out["reinforcement"] = reinforcement(case, sctx)
    return out


def station_cases(study: Study, sid: str, batch: str) -> dict:
    """`kapacitet station cases`: this station's cases of the batch, in its own process."""
    import warnings

    warnings.filterwarnings("ignore")
    work = json.loads((batch_dir(study, batch) / "work" / f"{sid}.json").read_text(encoding="utf-8"))
    sctx = station_context()
    results, errors = [], []
    if not sctx["ready"]:
        errors = [row_error(c["case_id"], "station_stale", station=sid) for c in work["cases"]]
    else:
        protection = _protection_rows(study.config.cases.protection)
        signature = read_signature(study)
        for case in work["cases"]:
            try:
                results.append(check_one(study, case, sctx, protection, signature))
            except CaseError as err:
                errors.append(row_error(case["case_id"], err.code, station=sid, **err.params))
            except Exception as exc:  # noqa: BLE001 - one case's failure is its row, not the batch's
                log.exception("case %s failed", case["case_id"])
                errors.append(row_error(case["case_id"], "station_failed", station=sid, detail=cause(exc)))
    out = {"station": sid, "batch": batch, "fingerprint": sctx["fingerprint"], "mv_band_pu": list(sctx["band"]),
           "queues": sctx["queues"], "results": results, "errors": errors}
    write_json_atomic(station_output(batch), out, default=_plain)
    return out


def _plain(o):
    import numpy as np

    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


# ---------------------------------------------------------------- the Swedish texts

def requested_sv(req: dict) -> str:
    from .publish import sv

    parts = []
    if req.get("pv_kwp", 0) > 0:
        ac = f", växelriktare {sv(req['pv_kw_ac'], 1)} kW" if req.get("pv_kw_ac", 0) > 0 else ""
        ph = {1: ", enfas", 3: ", trefas"}.get(req.get("pv_phases", 0), "")
        parts.append(f"solceller {sv(req['pv_kwp'], 1)} kWp{ac}{ph}")
    if req.get("bat_kwh", 0) > 0:
        parts.append(f"batteri {sv(req['bat_kwh'], 1)} kWh")
    if req.get("ev_kw", 0) > 0:
        ph = {1: ", enfas", 3: ", trefas"}.get(req.get("ev_phases", 0), "")
        parts.append(f"laddbox {sv(req['ev_kw'], 1)} kW{ph}")
    if req.get("hp"):
        parts.append("värmepump")
    return ", ".join(parts) or "–"


def reason_texts(r: dict) -> tuple[list[str], list[str]]:
    """The case's reasons as codes and in Swedish: the static study's for each part, then the rule's."""
    from . import texts

    codes, words = [], []
    for part, s in (r.get("static") or {}).items():
        if s.get("reason_code"):
            codes.append(f"{part}:{s['reason_code']}")
            words.append(texts.static_reason(s["reason_code"], s.get("reason_value")))
    for reason in r.get("reasons") or []:
        codes.append(reason["code"])
        words.append(texts.rule_reason(reason["code"], reason.get("params")))
    # a charger and a heat pump share the winter peak's verdict: said once
    return codes, list(dict.fromkeys(words))


def _day(text: str) -> date:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def _months_later(d: date, months: int) -> date:
    import calendar

    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _protection_sv(r: dict) -> str:
    from .publish import sv

    if r.get("protection_mode") != "kapacitet":
        return PROTECTION_SV
    row = r.get("protection")
    if not row or "error" in (row or {}):
        return f"kunde inte beräknas i Kapacitet+ ({(row or {}).get('error', 'ingen uppgift')})."
    verdict = "når" if row.get("ok") else "når inte"
    return (f"beräknat i Kapacitet+ (IEC 60909, minsta enfasiga fel): {sv(row['ik1_min_a'], 0)} A vid huset; "
            f"huvudsäkringen {row['main_fuse_a']} A behöver {sv(row['gate_5s_a'], 0)} A för att lösa ut inom "
            f"5 s, så felströmmen {verdict} dit. Bekräftas i dpPower.")


def annex(r: dict) -> tuple[str, dict]:
    """The case's annex in Swedish: as Markdown for a person and as JSON for a program."""
    from .publish import sv

    codes, words = reason_texts(r)
    bind = r.get("binding_mv_pu") or {}
    band = r.get("mv_band_pu") or []
    lines = [f"# Bilaga: teknisk bedömning av anslutningsärende {r['case_id']}", "",
             "Framtagen av Kapacitet+. Det är ett förslag, inte ett beslut: en handläggare hos nätföretaget "
             "prövar ärendet och fattar beslutet. Kapacitet+ föreslår aldrig att en ansökan avslås.", "",
             "| | |", "|---|---|",
             f"| Ärende | {r['case_id']} |", f"| Anslutningspunkt | {r['connection_point_id']} |",
             f"| Nätstation | {r['station']} |", f"| Ansökan mottagen | {r['received_at']} |",
             f"| Det som efterfrågas | {requested_sv(r['request'])} |",
             f"| Förslag | {PROPOSAL_SV[r['proposal']]} |",
             f"| Bedömning | {r['assessment_id']}, {r['assessed_at']} |",
             f"| Försegling (SHA-256) | {r['seal_digest'] or '–'} |",
             f"| Metodversion | {r['method_version']} |", f"| Stationens fingeravtryck | {r['fingerprint']} |", "",
             "## Skäl", ""]
    lines += [f"- {w}" for w in words] or ["- –"]
    lines += ["", "## Mellanspänningen", "",
              f"Bedömningen är gjord för stationens spänningsband på mellanspänningssidan, "
              f"{sv(band[0])}–{sv(band[1])} pu: produktion vid bandets båda ändar, där den lägre gäller, "
              f"och förbrukning vid den låga änden." if len(band) == 2 else "–"]
    if bind.get("envelope") is not None:
        lines.append(f"Exportgränsen bestäms vid {sv(bind['envelope'])} pu och den statiska prövningen av "
                     f"solcellerna vid {sv(bind['static'])} pu.")
    if r.get("curtailment_mean_pct") is not None:
        lines += ["", "## Nedreglering", "",
                  f"Med den dynamiska exportgränsen nedregleras i genomsnitt {sv(r['curtailment_mean_pct'], 1)} % "
                  f"av solelen, och {sv(r['curtailment_p90_pct'], 1)} % ett år av tio."]
    lines += ["", "## Huvudsäkring och faser", ""]
    if r.get("fuse") is not None:
        f = r["fuse"]
        lines.append(f"Huvudsäkringen {sv(f['fuse_a'], 0)} A {'räcker' if f['ok'] else 'räcker inte'} "
                     f"({sv(f['phase_current_a'], 0)} A i den mest belastade fasen).")
    if r.get("phases") is not None:
        lines.append("Fasbalansen " + ("håller gränsen." if r["phases"]["ok"] else "överskrider gränsen i alla faser."))
    if r.get("fuse") is None and r.get("phases") is None:
        lines.append("Ingen ny förbrukning eller enfasig utrustning att pröva.")
    protection = _protection_sv(r)
    lines += ["", "## Kortslutning och selektivitet", "", protection[:1].upper() + protection[1:]]
    if r.get("queue"):
        q = r["queue"]
        lines += ["", "## Kön", "", f"Bedömd mot nätet med {q.get('approved', 0)} godkända anslutningar i kön "
                                     f"(version {q.get('version')})."]
    doc = {"case_id": r["case_id"], "station": r["station"], "connection_point_id": r["connection_point_id"],
           "received_at": r["received_at"], "request": r["request"], "requested_sv": requested_sv(r["request"]),
           "proposal": r["proposal"], "proposal_sv": PROPOSAL_SV[r["proposal"]], "reason_codes": codes,
           "reasons_sv": words, "static": r["static"], "curtailment_mean_pct": r.get("curtailment_mean_pct"),
           "curtailment_p90_pct": r.get("curtailment_p90_pct"), "fuse": r.get("fuse"), "phases_ok": r.get("phases_ok"),
           "mv_band_pu": band, "binding_mv_pu": bind, "protection_sv": _protection_sv(r),
           "assessment_id": r["assessment_id"], "seal_digest": r["seal_digest"],
           "method_version": r["method_version"], "fingerprint": r["fingerprint"], "rule": r["rule"]}
    return "\n".join(lines) + "\n", doc


def drafts(study: Study, r: dict) -> dict[str, str]:
    """The drafts this case calls for (templates/cases_sv), filled in."""
    from .publish import render, sv

    cfg = study.config
    received = _day(r["received_at"])
    common = {"case_id": r["case_id"], "dso_name": cfg.dso.name or "nätföretaget",
              "connection_point_id": r["connection_point_id"], "station": r["station"],
              "received_at": received.isoformat(), "requested": requested_sv(r["request"]),
              "assessed_at": r["assessed_at"][:10], "method_version": r["method_version"],
              "proposal_sv": PROPOSAL_SV[r["proposal"]], "assessment_id": r["assessment_id"],
              "seal_digest": r["seal_digest"] or "–"}
    days = cfg.cases.processing_days
    processing = (f"Nätföretaget planerar att lämna besked senast {(received + timedelta(days=days)).isoformat()}, "
                  f"{days} dagar efter att ansökan kom in." if days else
                  "Ingen handläggningstid är angiven i studien ([cases] processing_days), så inget datum föreslås.")
    out = {"tidsplan": render((TEMPLATES / "tidsplan.md").read_text(encoding="utf-8"),
                              {**common, "processing": processing})}
    _, words = reason_texts(r)
    if r["proposal"] == "review":
        out["status_3m"] = render((TEMPLATES / "status_3m.md").read_text(encoding="utf-8"), {
            **common, "reasons": "\n".join(f"- {w}" for w in words) or "- –",
            "next_step": "En handläggare prövar ärendet och kontaktar den som ansökt om vad som behövs för en "
                         "anslutning.",
            "status_date": _months_later(received, 3).isoformat()})
    rf = r.get("reinforcement")
    if rf is not None:
        now = rf["current"]
        trafo_now = f"{now['std_type'] or 'egen typ'}, {sv(now['sn_mva'] * 1000, 0)} kVA"
        if rf["next"] is None:
            proposal = ("Det finns ingen större standardtransformator för samma spänningar i biblioteket, så "
                        "Kapacitet+ föreslår ingen.")
            verification = "Ingen kontroll gjord."
        else:
            n = rf["next"]
            proposal = f"Byt transformatorn till {n['std_type']} ({sv(n['sn_mva'] * 1000, 0)} kVA)."
            ends = ", ".join(f"{sv(float(e))} pu" for e in rf["by_end"])
            verification = (f"Den statiska prövningen är gjord igen med den nya transformatorn ({ends}): "
                            + ("den godkänner hela anslutningen." if rf["approved"] else
                               "den godkänner ändå inte hela anslutningen, så gränsen är inte bara "
                               "transformatorn (kabel eller spänning) och förstärkningen behöver projekteras."))
        rejected = {part: s for part, s in r["static"].items() if not s["approved"]}
        static_words = reason_texts({"static": rejected})[1]
        out["forstarkning"] = render((TEMPLATES / "forstarkning.md").read_text(encoding="utf-8"), {
            **common, "trafo_now": trafo_now, "static_reasons": "\n".join(f"- {w}" for w in static_words) or "- –",
            "proposal": proposal, "verification": verification, "protection": _protection_sv(r)})
    return out


# ---------------------------------------------------------------- the batch

def _row(r: dict) -> dict:
    codes, words = reason_texts(r)
    bind = r.get("binding_mv_pu") or {}
    end = bind.get("envelope", bind.get("consumption"))
    return {"case_id": r["case_id"], "received_at": r["received_at"], "station": r["station"],
            "connection_point_id": r["connection_point_id"], "proposal": r["proposal"],
            "reason_codes": ";".join(codes), "reason_sv": " | ".join(words),
            "curtailment_mean_pct": r.get("curtailment_mean_pct"), "curtailment_p90_pct": r.get("curtailment_p90_pct"),
            "fuse_ok": r.get("fuse_ok"), "phases_ok": r.get("phases_ok"),
            "binding_mv_pu": None if end is None else float(end), "rule": r.get("rule"),
            "assessment_id": r["assessment_id"], "seal_digest": r["seal_digest"],
            "method_version": r["method_version"], "fingerprint": r["fingerprint"],
            "decision_dso": r.get("decision_dso"), "error": None, "error_text": None}


def confusion(rows: list[dict]) -> list[dict]:
    """Proposals (rows) against the DSO's decisions (columns), counting the cases with both."""
    decided = sorted({r["decision_dso"] for r in rows if r.get("decision_dso") and r.get("proposal")})
    out = []
    for p in PROPOSALS:
        line = {"proposal": p}
        for d in decided:
            line[d] = sum(1 for r in rows if r.get("proposal") == p and r.get("decision_dso") == d)
        line["total"] = sum(line[d] for d in decided)
        out.append(line)
    return out


def run(study: Study, csv_path: str | Path, batch: str | None = None, compare: bool = False,
        jobs: int | None = None, runner: Runner | None = None, allow_git: bool = False) -> dict:
    """Check a batch of cases (the module's docstring); returns the rows and where they went."""
    from .. import seals

    csv_path = Path(csv_path)
    batch = batch or csv_path.stem
    folder = batch_dir(study, batch)
    cases, errors = read_cases(csv_path)
    by_station, unplaced = group(study, cases)
    errors += unplaced
    work = {sid: {"batch": batch, "cases": cs} for sid, cs in sorted(by_station.items())}
    got = run_stations(study, "cases", batch, work, jobs=jobs, runner=runner, allow_git=allow_git)
    results = []
    for sid, doc in got.items():
        if "failed" in doc:
            why = process_failure(study, sid, doc)
            errors += [row_error(c["case_id"], "station_failed", station=sid, detail=why) for c in work[sid]["cases"]]
            continue
        results += doc["results"]
        errors += doc["errors"]
    given = {c["case_id"]: c for c in cases}
    order = {c["case_id"]: i for i, c in enumerate(cases)}
    rows = [_row(r) for r in results]
    for e in errors:
        c = {} if e["error"] == "duplicate_case" else given.get(e["case_id"], {})
        rows.append({**dict.fromkeys(VERDICT_COLUMNS), "case_id": e["case_id"],
                     "received_at": e.get("received_at", c.get("received_at")),
                     "connection_point_id": e.get("connection_point_id", c.get("connection_point_id")),
                     "station": e.get("station") or None,
                     "error": e["error"], "error_text": e["error_text"], "decision_dso": c.get("decision_dso")})
    rows.sort(key=lambda r: (order.get(r["case_id"], len(order)), r["case_id"]))
    dialect = study.config.output.csv_dialect
    written = write_csv(folder, "verdicts", rows, VERDICT_COLUMNS, dialect)
    for r in results:
        md, doc = annex(r)
        write_text_atomic(folder / "annex" / f"{r['case_id']}.md", md)
        write_json_atomic(folder / "annex" / f"{r['case_id']}.json", doc, ensure_ascii=False)
        for name, text in drafts(study, r).items():
            write_text_atomic(folder / "drafts" / r["case_id"] / f"{name}.md", text)
    matrix = None
    if compare:
        matrix = confusion(rows)
        cols = ["proposal", *sorted({k for line in matrix for k in line} - {"proposal", "total"}), "total"]
        written += write_csv(folder, "confusion", matrix, cols, dialect)
    sealed = {"batch": batch, "rows": rows}
    with seals.chain_at(study.evidence):
        entry = seals.seal("cases", f"{batch}.{seals.digest(sealed)[:12]}", sealed)
    return {"batch": batch, "folder": folder, "rows": rows, "results": results, "errors": errors,
            "paths": written, "confusion": matrix, "seal": entry}


def sign_rule(study: Study, by: str, kinds: list[str], max_pv_kwp: float, valid_until: str) -> dict:
    """rule_signature.json: who signs the automatic rule for this study's cases, for which kinds of
    request, up to what PV size and until when, with the thresholds in force; sealed as
    `rule_signature` in the study's chain. A case's row says whether the signed rule covers it."""
    from pydantic import ValidationError

    from .. import dso, seals
    from .publish import _signer

    from ..config import assumptions

    name, _, role = _signer(by).partition(",")
    # the thresholds a station's process holds the rule to: the DSO settings' defaults, which are
    # the study's [assumptions] where it overrides them (this process may not read study.toml's)
    over = study.config.overrides()

    def value(key: str) -> float:
        return float(over.get(key, assumptions()[key].value))

    z = value("z_star_pct")
    try:
        th = dso.SignedThresholds(curtailment_mean_pct=z, curtailment_p90_pct=z,
                                  ev_unserved_pct=value("ev_unserved_limit_pct"))
        sig = dso.signature_of(dso.RuleSignatureIn(signed_by=name.strip(), role=role.strip(),
                                                   request_kinds=kinds, max_pv_kwp=max_pv_kwp,
                                                   thresholds=th, valid_until=valid_until))
    except ValidationError as err:
        raise StudyError("bad_signature", message="; ".join(e["msg"] for e in err.errors())) from None
    doc = dso.signature_doc(sig)
    write_json_atomic(study.root / "rule_signature.json", doc)
    with seals.chain_at(study.evidence):
        entry = seals.seal("rule_signature", dso.signature_digest(doc)[:16], doc)
    return {**doc, "seal": entry}


__all__ = ["COLUMNS", "ROW_ERRORS", "VERDICT_COLUMNS", "CaseError", "annex", "assess", "check_one", "confusion",
           "drafts", "group", "locate", "read_cases", "run", "run_stations", "station_cases"]
