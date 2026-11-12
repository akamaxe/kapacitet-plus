"""Whether a station's model holds against the DSO's own acceptance criteria ([validation]).

`study validate` compares a station's model with its measurements and writes the errors
(engine/validation_measured.py, results/validation_measured.json). On its own that decides
nothing: an error is only large or small against a tolerance, and the tolerance is the DSO's to
set. The verdict here is read from that file and the criteria as they stand now, so a tolerance
set or changed after the comparison applies at once, without replaying anything:

    not_validated   `study validate` has not run for the station
    not_measured    it ran, and the station has no meter readings, or none for a criterion that is set
    no_criterion    the model was compared with the measurements, and the DSO has set no criterion
    within          every criterion that is set holds
    outside         one of them does not: the station is not published (study/publish.py)

Only `within` is ever called validated.

The voltage criterion is judged at the measured MV voltage only (inputs/mv/<station>.csv). Replayed
at the two ends of the planning band instead, the error is the width of the band and says nothing
about the model, so without a measured MV voltage the voltage criterion is `not_measured`. The
transformer's loading depends little on the MV voltage: it is judged at the measured one when
there is one, otherwise at the end of the band where its error is larger.
"""

from __future__ import annotations

import json

from . import Study
from .config import Validation

STATUSES = ("not_validated", "not_measured", "no_criterion", "within", "outside")
FILE = "validation_measured.json"
# what a publication says of each status, and of the engine's own "no meter readings"
SV = {
    "not_validated": "ingen validering körd",
    "not_measured": "mätvärden saknas",
    "no_criterion": "jämförd med mätningar, inget godkännandekriterium angivet",
    "within": "validerad: inom nätföretagets toleranser",
    "outside": "utanför nätföretagets toleranser, publiceras inte",
}
CRITERION_SV = {
    "voltage_p95_pu": "spänning hos kund, 95:e percentilen av avvikelsen (p.u.)",
    "transformer_p95_pct": "transformatorbelastning, 95:e percentilen av avvikelsen (procentenheter)",
    "min_metered_share": "andel kunder med mätvärden",
}
BASIS_SV = {"measured": "uppmätt mellanspänning", "band_low": "spänningsbandets lägre ände",
            "band_high": "spänningsbandets högre ände"}


def read(study: Study, sid: str) -> dict | None:
    """The station's validation_measured.json, or None when `study validate` has not written one."""
    path = study.station_dir(sid) / "processed" / "results" / FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def _p95(block: dict | None) -> float | None:
    return float(block["p95"]) if block and block.get("n") and block.get("p95") is not None else None


def verdict(doc: dict | None, criteria: Validation, customers: int | None) -> dict:
    """{"status": one of STATUSES, "checks": one per criterion that is set, each {"criterion",
    "limit", "value" (None when the station has no measurement for it), "basis", "ok"}}."""
    if doc is None:
        return {"status": "not_validated", "checks": []}
    measured = doc.get("measured") or {}
    bases = measured.get("bases") or {}
    if not bases:
        return {"status": "not_measured", "checks": []}
    if not criteria.entered:
        return {"status": "no_criterion", "checks": []}
    checks = []
    if criteria.voltage_p95_pu is not None:
        value = _p95((bases.get("measured") or {}).get("voltage_pu"))
        checks.append({"criterion": "voltage_p95_pu", "limit": criteria.voltage_p95_pu, "value": value,
                       "basis": "measured", "ok": None if value is None else value <= criteria.voltage_p95_pu + 1e-12})
    if criteria.transformer_p95_pct is not None:
        names = ["measured"] if "measured" in bases else sorted(bases)
        found = [(v, name) for name in names if (v := _p95(bases[name].get("transformer_pct"))) is not None]
        value, basis = max(found) if found else (None, names[0])
        checks.append({"criterion": "transformer_p95_pct", "limit": criteria.transformer_p95_pct, "value": value,
                       "basis": basis, "ok": None if value is None else value <= criteria.transformer_p95_pct + 1e-12})
    if criteria.min_metered_share is not None:
        n = measured.get("metered_customers")
        share = float(n) / float(customers) if n is not None and customers else None
        checks.append({"criterion": "min_metered_share", "limit": criteria.min_metered_share, "value": share,
                       "basis": None, "ok": None if share is None else share >= criteria.min_metered_share - 1e-12})
    if any(c["ok"] is False for c in checks):
        status = "outside"
    elif any(c["ok"] is None for c in checks):
        status = "not_measured"
    else:
        status = "within"
    return {"status": status, "checks": checks}


def of(study: Study, sid: str, customers: int | None) -> dict:
    """The station's verdict against the study's criteria as they stand now."""
    return verdict(read(study, sid), study.config.validation, customers)


__all__ = ["BASIS_SV", "CRITERION_SV", "FILE", "STATUSES", "SV", "of", "read", "verdict"]
