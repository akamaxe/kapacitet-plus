"""`study review`: one run's figures as a person checks them before they are signed.

    runs/<Q>/review.csv          every station of runs/<Q>/stations.parquet, the one with the least
                                 margin first, with what to look at (`flags`) and the corrections
                                 that apply to it
    runs/<Q>/corrections.csv     the reviewer's own file, which the tool reads and never writes:
                                 station_id, field, value, reason, by

A correction never overwrites a computed value: it is an overlay, shown in columns of its own
beside the figure it corrects (`corrected_*`), and the publication carries both. It can only make
a figure more cautious, so the fields it may set are:

    generation_kw, consumption_kw   a lower value (kW, at least 0, at most the published one)
    hidden                          true: the station is left out of the publication
    note                            a remark published with the station

A correction that cannot apply (an unknown station or field, a value that is not a number, a
higher kW, no reason) is a finding of the review, listed and not applied.

The margin of a station is the smaller of its published generation and consumption capacity
(after the upstream caps). A station without a figure on either side (a finding, or not computed
yet) has none and comes first, since that is what most needs a person.
"""

from __future__ import annotations

from pathlib import Path

from . import Study, StudyError
from . import csvio

CORRECTABLE = ("generation_kw", "consumption_kw", "hidden", "note")
CORRECTION_COLUMNS = ("station_id", "field", "value", "reason", "by")
TRUE = ("1", "true", "yes", "ja", "x", "sant")
COLUMNS = [
    "station_id", "name", "status", "customers", "hidden", "margin_kw", "flags",
    "generation_status", "published_generation_kw", "published_generation_limited_by", "generation_limit",
    "binding_mv_pu", "consumption_device", "consumption_status", "published_consumption_kw",
    "published_consumption_limited_by", "consumption_limit", "upstream_complete",
    "worst_generation_kw", "worst_consumption_kw",
    "generation_baseline_violation_hours", "consumption_baseline_violation_hours",
    "full_year_violations", "nonconverged_hours", "conditional_generation_status",
    "published_conditional_generation_kw", "method_version",
    "corrected_generation_kw", "corrected_consumption_kw", "corrected_hidden", "correction_note",
    "correction_reason", "correction_by",
]


def run_dir(study: Study, q: str) -> Path:
    from . import runs

    if not runs.valid(q):
        raise StudyError("config", message=f"{q!r} is not a run id: {runs.EXAMPLE}")
    return study.runs / q


def load_rows(study: Study, q: str) -> list[dict]:
    """The rows of runs/<q>/stations.parquet (`study collect`)."""
    import pyarrow.parquet as pq

    path = run_dir(study, q) / "stations.parquet"
    if not path.is_file():
        raise StudyError("not_collected", quarter=q, path=str(study.root))
    return pq.read_table(path).to_pylist()


def margin(row: dict) -> float | None:
    """The smaller published figure; None when either side has none (a finding, or not computed)."""
    vals = [row.get("published_generation_kw"), row.get("published_consumption_kw")]
    return None if any(v is None for v in vals) else float(min(vals))


def flags(row: dict, pinned: str) -> list[str]:
    out = []
    if row.get("generation_status") is None and row.get("consumption_status") is None:
        out.append("not_computed")
    for side in ("generation", "consumption"):
        if row.get(f"{side}_status") == "baseline_violates":
            out.append(f"{side}_baseline_violation")
    if row.get("generation_status") is not None and not row.get("upstream_complete"):
        out.append("upstream_incomplete")
    if row.get("full_year_violations"):
        out.append("violations")
    if row.get("nonconverged_hours"):
        out.append("nonconverged")
    if row.get("method_version") not in (None, pinned):
        out.append("method_mismatch")
    if row.get("hidden"):
        out.append("hidden")
    return out


def corrections(study: Study, q: str, rows: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """({sid: {field: {"value", "reason", "by"}}}, findings) from runs/<q>/corrections.csv: only the
    corrections that can apply; each one that cannot is a finding."""
    by_id = {r["station_id"]: r for r in rows}
    out: dict[str, dict] = {}
    findings: list[str] = []
    for i, c in enumerate(csvio.read(run_dir(study, q) / "corrections.csv"), start=2):
        sid, field, text = c.get("station_id", ""), c.get("field", "").lower(), c.get("value", "")
        where = f"corrections.csv line {i}"
        if sid not in by_id:
            findings.append(f"{where}: the run has no station {sid!r}")
            continue
        if field not in CORRECTABLE:
            findings.append(f"{where}: {field!r} cannot be corrected (only {', '.join(CORRECTABLE)})")
            continue
        if not c.get("reason"):
            findings.append(f"{where}: a correction needs its reason")
            continue
        if field in ("generation_kw", "consumption_kw"):
            try:
                value = csvio.number(text)
            except ValueError:
                findings.append(f"{where}: {text!r} is not a number")
                continue
            computed = by_id[sid].get(f"published_{field}")
            if value is None or value < 0:
                findings.append(f"{where}: a capacity correction is a number of kW, at least 0")
                continue
            if computed is None or value > float(computed):
                findings.append(f"{where}: {field} {value:g} is not below the computed {computed} kW; a correction "
                                "can only lower a figure")
                continue
        elif field == "hidden":
            if text.lower() not in TRUE:
                findings.append(f"{where}: hidden can only be set to true (a correction never publishes more)")
                continue
            value = True
        else:
            value = text
        if field in out.get(sid, {}):
            findings.append(f"{where}: {sid} {field} is corrected twice; the first is kept")
            continue
        out.setdefault(sid, {})[field] = {"value": value, "reason": c["reason"], "by": c.get("by") or None}
    return out, findings


def _joined(fix: dict, key: str) -> str | None:
    vals = sorted({str(v[key]) for v in fix.values() if v.get(key)})
    return "; ".join(vals) or None


def review(study: Study, q: str) -> dict:
    """runs/<q>/review.csv (and its machine copy). Returns {"paths", "rows", "findings"}."""
    rows = load_rows(study, q)
    fixes, findings = corrections(study, q, rows)
    pinned = study.config.dso.method_version
    out = []
    for r in rows:
        fix = fixes.get(r["station_id"], {})
        out.append({**{c: r.get(c) for c in COLUMNS if c in r}, "margin_kw": margin(r),
                    "flags": ";".join(flags(r, pinned)) or None,
                    "corrected_generation_kw": (fix.get("generation_kw") or {}).get("value"),
                    "corrected_consumption_kw": (fix.get("consumption_kw") or {}).get("value"),
                    "corrected_hidden": True if "hidden" in fix else None,
                    "correction_note": (fix.get("note") or {}).get("value"),
                    "correction_reason": _joined(fix, "reason"), "correction_by": _joined(fix, "by")})
    out.sort(key=lambda r: (r["margin_kw"] is not None, r["margin_kw"] or 0.0, r["station_id"]))
    paths = csvio.write(run_dir(study, q), "review", out, COLUMNS, study.config.output.csv_dialect)
    return {"paths": paths, "rows": out, "findings": findings}


__all__ = ["COLUMNS", "CORRECTABLE", "corrections", "flags", "load_rows", "margin", "review", "run_dir"]
