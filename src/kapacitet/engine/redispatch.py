"""The annual report on non-market-based redispatch (Regulation (EU) 2019/943, art. 13.4).

A DSO that curtails generation without a market reports, every year, how much it did, of what
resource, and why. Here that is the solar export the conditional agreements held back, read from
the operation stage on the basis that is actually issued: the day-ahead envelopes, never the
perfect-foresight planning figure, which nobody can send to an inverter.

    redispatch.json    the figures: energy (MWh), hours, installations, share of their potential
                       export, compensation paid, binding components, market alternatives recorded
    redispatch_sv.txt  the same figures as a page in Swedish, to file

Every figure is the operation stage's (results/operation/index.json and the combination's
compliance.json) or the DSO's (its settings, its market alternatives log): nothing new is computed.
"""

from __future__ import annotations

import json

from .. import dso, market
from ..paths import processed, processed_ro

BASIS = "dayahead"
LEGAL_BASIS = "Regulation (EU) 2019/943, art. 13.4"
COMMAND = "python -m kapacitet redispatch-report"
OPERATION = "python -m kapacitet compute --stage extra --only operation"


class RedispatchError(RuntimeError):
    """The operation stage has not produced what the report needs."""


def _read(*parts: str) -> dict:
    path = processed_ro(*parts)
    if not path.exists():
        raise RedispatchError(f"{'/'.join(parts)} is missing: run {OPERATION}")
    return json.loads(path.read_text(encoding="utf-8"))


def build(year: int | None = None, policy: str | None = None) -> dict:
    """The report for the operation stage's year under the DSO's sharing rule (or `policy`)."""
    index = _read("results", "operation", "index.json")
    if year is not None and int(year) != int(index["year"]):
        raise RedispatchError(f"the operation stage covers {index['year']}, not {year}")
    settings = dso.load()
    policy = policy or settings.allocation_policy
    combo = (index.get("combos", {}).get(BASIS) or {}).get(policy)
    if not combo:
        raise RedispatchError(f"{BASIS}/{policy} was not computed: run {OPERATION}")
    if not combo.get("verified"):
        raise RedispatchError(f"{BASIS}/{policy} did not verify over the year, so it is not reported")
    comp = _read("results", "operation", BASIS, policy, "compliance.json")
    per = comp.get("per_customer", [])
    kwh = float(combo["curtailed_kwh"])
    alternatives = market.in_year(int(index["year"]))
    kinds = sorted({a["kind"] for e in alternatives for a in e.get("alternatives", [])})
    return {
        "year": int(index["year"]),
        "legal_basis": LEGAL_BASIS,
        "redispatch": "non_market_based",
        "resource": "solar_generation",
        "basis": BASIS,
        "policy": policy,
        "curtailed_kwh": kwh,
        "curtailed_mwh": round(kwh / 1000.0, 3),
        "hours": int(combo["hours_limited"]),
        "hours_needed": int(comp.get("activations_necessary_hours", index.get("necessary_hours", 0)) or 0),
        "hours_not_needed": int(combo.get("activations_unnecessary_hours") or 0),
        "installations": sum(1 for c in per if float(c.get("curtailed_kwh") or 0) > 0),
        "agreements": int(comp.get("conditional_agreements", len(per))),
        "potential_export_kwh": float(comp["non_firm_export_kwh"]),
        "share_of_potential_export_pct": float(combo["curtailed_share_of_export_pct"]),
        "compensation_sek": float(settings.redispatch_compensation_sek),
        "compensation_entered": settings.redispatch_compensation_sek > 0,
        "binding_components_hours": {k: int(v) for k, v in (comp.get("binding_constraint_hours") or {}).items()},
        "binding_components_needed_hours": {k: int(v) for k, v in
                                            (comp.get("binding_constraint_necessary_hours") or {}).items()},
        "verified_violations": int(comp.get("verified_violations", 0)),
        "market_alternatives": {
            "recorded": len(alternatives),
            "kinds": kinds,
            "records": [{k: e.get(k) for k in ("id", "recorded_at", "scope", "ref", "decided_by")}
                        | {"kinds": [a["kind"] for a in e.get("alternatives", [])]} for e in alternatives],
        },
    }


# what each part of the network and each kind of alternative is called on the Swedish page
_SV_COMPONENT = {"transformer": "transformator", "voltage": "spänning"}
_SV_KIND = {"local_flexibility_market": "lokal flexibilitetsmarknad", "bilateral_flexibility": "bilateral flexibilitet",
            "network_tariff": "nättariff", "none_available": "inget tillgängligt", "other": "annat"}
_SV_POLICY = {"maxmin": "maxmin", "prorata": "proportionell", "propfair": "proportionellt rättvis",
              "maxtotal": "största totala export"}


def _n(value: float, digits: int = 0) -> str:
    """A number as a Swedish page writes it: a space for thousands and a decimal comma."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", " ").replace(".", ",")


def text_sv(r: dict) -> str:
    """The report as a page in Swedish."""
    def comp(name: str) -> str:
        return f"kabel {name[5:]}" if name.startswith("line ") else _SV_COMPONENT.get(name, name)

    lines = [
        f"Icke-marknadsbaserad omdirigering, rapportår {r['year']}",
        "=" * 58,
        "Grund: förordning (EU) 2019/943, artikel 13.4",
        "Resurs: solel från kunder med villkorat avtal",
        "Underlag: exportgränser utfärdade dagen före (aldrig planeringssiffran med perfekt framförhållning)",
        f"Fördelningsregel: {_SV_POLICY.get(r['policy'], r['policy'])}",
        "",
        f"Nedreglerad energi                {_n(r['curtailed_mwh'], 1)} MWh",
        f"Timmar med nedreglering           {_n(r['hours'])} (varav {_n(r['hours_needed'])} behövdes)",
        f"Anläggningar som nedreglerades    {_n(r['installations'])} av {_n(r['agreements'])} villkorade avtal",
        f"Andel av möjlig export            {_n(r['share_of_potential_export_pct'], 1)} %"
        f" (av {_n(r['potential_export_kwh'] / 1000.0, 1)} MWh)",
        f"Ersättning som betalats           {_n(r['compensation_sek'])} SEK"
        + ("" if r["compensation_entered"] else " (ingen angiven av nätbolaget)"),
        f"Timmar med överträdelse           {_n(r['verified_violations'])}",
        "",
        "Begränsande komponent (timmar)",
    ]
    needed = r.get("binding_components_needed_hours") or {}
    for name, h in sorted(r["binding_components_hours"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {comp(name):<30}  {_n(h)}" + (f" (varav {_n(needed[name])} behövdes)" if name in needed else ""))
    ma = r["market_alternatives"]
    lines += ["", f"Marknadsbaserade alternativ som registrerats: {_n(ma['recorded'])}"]
    for e in ma["records"]:
        ref = e.get("ref") or {}
        what = f"kund {ref['cid']}" if "cid" in ref else f"{ref.get('start')} till {ref.get('end')}"
        kinds = ", ".join(_SV_KIND.get(k, k) for k in e["kinds"])
        by = f", {e['decided_by']}" if e.get("decided_by") else ""
        lines.append(f"  {e['recorded_at'][:10]}  {what}: {kinds}{by}")
    if not ma["records"]:
        lines.append("  inga: nätbolaget har inte registrerat några")
    lines += ["", "Simulerade siffror från ett syntetiskt nät, inte uppmätta: underlag, aldrig en juridisk bedömning."]
    return "\n".join(lines) + "\n"


def write(year: int | None = None, policy: str | None = None) -> dict:
    """Build the report and write results/redispatch.json and redispatch_sv.txt."""
    from ..atomic import write_json_atomic, write_text_atomic

    report = build(year, policy)
    from .stamp import stamped

    write_json_atomic(processed("results", "redispatch.json"), stamped(report))
    write_text_atomic(processed("results", "redispatch_sv.txt"), text_sv(report))
    return report
