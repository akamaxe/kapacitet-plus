"""Module 1's publication: `study sign` and `study publish`, from one collected run (a quarter such
as 2026Q4 or a month such as 2026-11, study/runs.py).

    runs/<run>/stations.parquet, inputs.json   `study collect`
    runs/<run>/review.csv, corrections.csv     `study review` (study/review.py)
    runs/<run>/signoff.json                    `study sign --by "name, role"`
    runs/<run>/publication/                    `study publish`:
        capacity.csv, capacity.machine.csv     one row per published cell (study/csvio.py)
        capacity.geojson                       the same cells on the map (WGS84)
        grunder_for_berakning.md               how the figures were computed, every value from the data
        validation_report.sv.md                the model against the DSO's measurements
        embed/                                 the embeddable map: index.html, embed.js, embed.css
                                               (study/embed.py), reading the two files above
        manifest.json                          what was published, from what, with each file's sha256
    runs/<run>/disclosure_report.csv           which station is in which cell: kept, never published

Signing seals the run's outputs: the sha256 of the run's files and of every station's results
(`outputs`), as a seal of kind `publication` in the study's own chain (evidence/, kapacitet.seals).
A publication is signed only while every one of those files is what was signed: a station
computed again, a correction added or a cap changed after the signature makes it a draft again,
and every file of it says UTKAST until someone signs anew. A study pinned to another method
version than this code's, or with a station computed by another, is not published at all.

The conditional (envelope) column appears only when [method] approval_reference is set, and a
station has a value in it only when its envelopes verified on every hour. The three-phase columns
appear only when at least one published station's three-phase check is "ok" or "limited"
(capacity.json's `three_phase`); a station whose phases are unknown has no value in them.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import shutil
from pathlib import Path

from .. import geo, seals
from ..atomic import write_json_atomic, write_text_atomic
from ..language import NOT_REVIEWED_SV
from . import Study, StudyError, code_version, file_digest
from . import csvio, disclosure, embed, runs, validation
from .config import METHOD_VERSION
from .review import corrections, load_rows, run_dir

SIGNOFF_SCHEMA = "kapacitet.signoff/1"
MANIFEST_SCHEMA = "kapacitet.publication/1"
DRAFT = "UTKAST"
SIGNED = "signerad"
RUN_FILES = ("stations.parquet", "inputs.json", "review.csv", "review.machine.csv", "corrections.csv")
STATION_FILES = ("capacity.json", "peak_check.json", "validation_measured.json")
TEMPLATE = Path(__file__).with_name("templates") / "grunder_for_berakning.sv.md"
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
CAPACITY_COLUMNS = ["status", "quarter", "cell_id", "level", "name", "stations", "customers",
                    "generation_kw", "generation_limited_by", "generation_shared_by_stations",
                    "consumption_device", "consumption_kw",
                    "consumption_limited_by", "consumption_shared_by_stations",
                    "three_phase_generation_kw", "three_phase_consumption_kw",
                    "conditional_generation_kw", "corrected_generation_kw",
                    "corrected_consumption_kw", "correction_note", "upstream_complete", "rounded_to_kw",
                    "finding", "lon", "lat"]
# every published figure in kW: a small cell's are rounded down together ([disclosure] round_kw)
KW_COLUMNS = ("generation_kw", "consumption_kw", "three_phase_generation_kw", "three_phase_consumption_kw",
              "conditional_generation_kw", "corrected_generation_kw", "corrected_consumption_kw")
# what a station outside the DSO's validation tolerances loses: every figure it would publish
WITHHELD = ("published_generation_kw", "published_consumption_kw", "published_conditional_generation_kw",
            "published_three_phase_generation_kw", "published_three_phase_consumption_kw")
VALIDATION_FINDING = "validation: outside the DSO's tolerances, not published"
DEVICE_SV = {"ev": "laddbox för elbil", "heat_pump": "värmepump"}
LEVEL_SV = {"station": "nätstation", "grid": "ruta", "zone": "område", "study": "hela studien"}
THREE_PHASE_COLUMNS = ("three_phase_generation_kw", "three_phase_consumption_kw")


# ---------------------------------------------------------------- checks

def check_method(study: Study, rows: list[dict] | None = None) -> None:
    """Refuse a study pinned to another method version, or a station computed by another."""
    pinned = study.config.dso.method_version
    if pinned != METHOD_VERSION:
        raise StudyError("method_version", pinned=pinned, current=METHOD_VERSION)
    other = sorted(r["station_id"] for r in rows or [] if r.get("method_version") not in (None, pinned))
    if other:
        raise StudyError("stale_method", ids=", ".join(other), pinned=pinned)


def outputs(study: Study, q: str, rows: list[dict]) -> dict[str, str]:
    """{path relative to the study: sha256} of everything a signature covers: study.toml, the run's
    files and each station's results."""
    out = {"study.toml": file_digest(study.toml)}
    folder = run_dir(study, q)
    for name in RUN_FILES:
        d = file_digest(folder / name)
        if d is not None:
            out[f"runs/{q}/{name}"] = d
    for r in rows:
        results = study.station_dir(r["station_id"]) / "processed" / "results"
        for name in STATION_FILES:
            d = file_digest(results / name)
            if d is not None:
                out[(results / name).relative_to(study.root).as_posix()] = d
    return out


def public_outputs(study: Study, q: str, rows: list[dict]) -> dict[str, str]:
    """`outputs` for the published manifest: the run-level digests and one digest over the station
    files, so no station id (a hidden or merged one) is named."""
    import hashlib

    full = outputs(study, q, rows)
    keep = {k: v for k, v in full.items() if not k.startswith("stations/")}
    station = sorted(f"{k}:{v}" for k, v in full.items() if k.startswith("stations/"))
    keep["stations"] = hashlib.sha256(chr(10).join(station).encode()).hexdigest()
    return keep


# ---------------------------------------------------------------- signing

def _seal_key(signoff: dict | None) -> dict | None:
    """The public key of a signoff's seal, with the sentence on how to verify; None for a draft
    never signed, or a signoff sealed before seals were signed."""
    kid = ((signoff or {}).get("seal") or {}).get("key_id")
    return seals.public_key_info(kid) if kid else None


def _signer(by: str) -> str:
    by = " ".join((by or "").split())
    name, _, role = by.partition(",")
    if not name.strip() or not role.strip():
        raise StudyError("bad_signer", by=by)
    return by


def sign(study: Study, q: str, by: str) -> dict:
    """runs/<q>/signoff.json, sealed in the study's chain as `publication` <q>.<n>."""
    by = _signer(by)
    rows = load_rows(study, q)
    check_method(study, rows)
    doc = {"schema": SIGNOFF_SCHEMA, "study": study.id, "dso": study.config.dso.name, "quarter": q, "by": by,
           "signed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "method_version": study.config.dso.method_version, "code": code_version(),
           "outputs": outputs(study, q, rows)}
    with seals.chain_at(study.evidence):
        n = 1 + sum(1 for e in seals._lines() if e.get("kind") == "publication" and str(e.get("id", "")).startswith(f"{q}."))
        entry = seals.seal("publication", f"{q}.{n}", doc)
    signed = {**doc, "seal": entry}
    write_json_atomic(run_dir(study, q) / "signoff.json", signed)
    return signed


def signoff_state(study: Study, q: str, rows: list[dict]) -> dict:
    """{"signed": bool, "signoff": the document or None, "changed": paths that differ from what was
    signed, "seal": the seal's verification}."""
    path = run_dir(study, q) / "signoff.json"
    if not path.is_file():
        return {"signed": False, "signoff": None, "changed": [], "seal": None}
    signed = json.loads(path.read_text(encoding="utf-8"))
    doc = {k: v for k, v in signed.items() if k != "seal"}
    with seals.chain_at(study.evidence):
        check = seals.verify("publication", (signed.get("seal") or {}).get("id", ""), current=doc)
    then, now = doc.get("outputs") or {}, outputs(study, q, rows)
    changed = sorted(k for k in set(then) | set(now) if then.get(k) != now.get(k))
    ok = bool(check.get("ok")) and not changed and doc.get("quarter") == q
    return {"signed": ok, "signoff": signed, "changed": changed, "seal": check}


# ---------------------------------------------------------------- the cells

def _finding(row: dict) -> str | None:
    bad = [f"{side}: {row.get(f'{side}_status') or 'not computed'}" for side in ("generation", "consumption")
           if row.get(f"{side}_status") != "ok"]
    if row.get("validation_status") == "outside":
        bad.append(VALIDATION_FINDING)
    return "; ".join(bad) or None


def judged(study: Study, rows: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """The rows with each station's verdict against the DSO's validation criteria as they stand now
    (study/validation.py), and the verdicts. A station outside a tolerance keeps its row and loses
    every figure it would publish: its cell then has none, and says why."""
    verdicts = {r["station_id"]: validation.of(study, r["station_id"], r.get("customers")) for r in rows}
    out = []
    for r in rows:
        status = verdicts[r["station_id"]]["status"]
        r = {**r, "validation_status": status}
        if status == "outside":
            r.update({k: None for k in WITHHELD})
        out.append(r)
    return out, verdicts


def _shared(members: list[dict], side: str) -> int | None:
    """How many stations share the upstream level that caps the cell's figure: the count of the
    member whose figure is the cell's (the lowest); None when the figure is a station's own."""
    figures = [(m.get(f"published_{side}_kw"), m.get(f"published_{side}_shared_by")) for m in members]
    if not figures or any(kw is None for kw, _ in figures):
        return None
    return min(figures, key=lambda t: t[0])[1]


def round_down(value: float | None, step: float | None) -> float | None:
    """`value` rounded down to a multiple of `step` (towards minus infinity: never a higher figure)."""
    if value is None or not step:
        return value
    return float(math.floor(float(value) / step + 1e-9) * step)


def _cell_finding(members: list[dict]) -> str | None:
    n = sum(1 for m in members if _finding(m))
    return f"{n} of {len(members)} stations have no figure" if n else None


def _conditional(study: Study, m: dict) -> float | None:
    """A station's conditional figure, only if it was computed under the approval reference now set."""
    ref = study.config.method.approval_reference.strip()
    if not ref or (m.get("approval_reference") or "").strip() != ref:
        return None
    return m.get("published_conditional_generation_kw")


def _three_phase(m: dict, side: str) -> float | None:
    """A station's three-phase figure, capped by the upstream chain, when its check publishes one."""
    from .collect import THREE_PHASE_FIGURES

    if m.get("three_phase_status") not in THREE_PHASE_FIGURES:
        return None
    return m.get(f"published_three_phase_{side}_kw")


def three_phase_shown(rows: list[dict]) -> bool:
    """Whether the publication has the three-phase columns: a station's check published a figure."""
    return any(_three_phase(r, side) is not None for r in rows for side in ("generation", "consumption"))


def _effective(row: dict, fix: dict, side: str) -> float | None:
    if row.get("validation_status") == "outside":
        return None                  # a correction lowers a figure; it never brings a withheld one back
    if side in ("generation", "consumption") and f"{side}_kw" in fix:
        return float(fix[f"{side}_kw"]["value"])
    return row.get(f"published_{side}_kw")


def cells(study: Study, q: str, rows: list[dict], fixes: dict, result: dict, status: str) -> list[dict]:
    """One row per published cell (suppressed cells are left out)."""
    by_id = {r["station_id"]: r for r in rows}
    approved = bool(study.config.method.approval_reference.strip())
    show3 = three_phase_shown([r for r in rows if result["stations"][r["station_id"]]["cell"]])
    out = []
    for cid, c in sorted(result["cells"].items(), key=lambda kv: (disclosure.LEVELS.index(kv[1]["level"]), kv[0])):
        if c["suppressed"]:
            continue
        members = [by_id[s] for s in c["stations"]]
        corrected = any(("generation_kw" in fixes.get(s, {}) or "consumption_kw" in fixes.get(s, {}))
                        for s in c["stations"])
        one = members[0] if len(members) == 1 and c["level"] == "station" else None
        info = c["info"]
        name = (one.get("name") if one else
                f"{info['size_m']} m" if c["level"] == "grid" else info.get("zone") or LEVEL_SV[c["level"]])
        row = {
            "status": status, "quarter": q, "cell_id": cid, "level": c["level"], "name": name,
            "stations": len(members), "customers": c["customers"],
            "generation_kw": disclosure.lowest([m.get("published_generation_kw") for m in members]),
            "consumption_kw": disclosure.lowest([m.get("published_consumption_kw") for m in members]),
            "generation_limited_by": one.get("published_generation_limited_by") if one else "lowest_of_stations",
            "consumption_limited_by": one.get("published_consumption_limited_by") if one else "lowest_of_stations",
            "generation_shared_by_stations": _shared(members, "generation"),
            "consumption_shared_by_stations": _shared(members, "consumption"),
            "consumption_device": study.config.method.consumption_device,
            "corrected_generation_kw": (disclosure.lowest([_effective(m, fixes.get(m["station_id"], {}), "generation")
                                                           for m in members]) if corrected else None),
            "corrected_consumption_kw": (disclosure.lowest([_effective(m, fixes.get(m["station_id"], {}), "consumption")
                                                            for m in members]) if corrected else None),
            "correction_note": "; ".join(fixes[s]["note"]["value"] for s in c["stations"]
                                         if "note" in fixes.get(s, {})) or None,
            "upstream_complete": all(bool(m.get("upstream_complete")) for m in members),
            "finding": _finding(one) if one else _cell_finding(members),
            "lon": one.get("lon") if one else None, "lat": one.get("lat") if one else None,
        }
        if approved:
            row["conditional_generation_kw"] = disclosure.lowest([_conditional(study, m) for m in members])
        if show3:
            # the same deployments checked with the unbalanced model, where the check publishes a figure
            for side in ("generation", "consumption"):
                row[f"three_phase_{side}_kw"] = disclosure.lowest([_three_phase(m, side) for m in members])
        # a small cell's figures are rounded down, so that two publications say less about one household
        step = study.config.disclosure.step_kw(c["customers"])
        row["rounded_to_kw"] = step
        for key in KW_COLUMNS:
            if key in row:
                row[key] = round_down(row[key], step)
        out.append(row)
    return out


def geojson(rows: list[dict], result: dict, meta: dict) -> dict:
    feats = [geo.feature(result["cells"][r["cell_id"]]["geometry"], {k: v for k, v in r.items() if k not in ("lon", "lat")})
             for r in rows]
    return geo.collection(feats, **meta)


# ---------------------------------------------------------------- Swedish text

def sv(x, decimals: int = 2) -> str:
    """A number as Swedish text: a decimal comma, trailing zeros dropped."""
    if x is None:
        return "–"
    text = f"{float(x):.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{float(x):.0f}"
    return text.replace("-", "−").replace(".", ",")


def _years(rows: list[dict]) -> str:
    ys = sorted({int(y) for r in rows for y in (r.get("years") or "").split(",") if y.strip()})
    if not ys:
        return "–"
    if ys == list(range(ys[0], ys[-1] + 1)) and len(ys) > 2:
        return f"{ys[0]}–{ys[-1]}"
    return ", ".join(str(y) for y in ys)


def _assumption(study: Study, key: str):
    from ..config import assumptions

    o = study.config.assumptions.get(key)
    return o.value if o is not None else assumptions()[key].value


def _three_phase_text(study: Study, shown: list[dict]) -> str:
    """The grunder's paragraph on the three-phase columns: which figure is which, and how many have one."""
    share = sv(100.0 * float(_assumption(study, "three_phase_min_known_share")), 0)
    with_figure = sum(1 for r in shown if any(_three_phase(r, s) is not None for s in ("generation", "consumption")))
    limited = sum(1 for r in shown if any(r.get(f"three_phase_{s}_status") == "limited" and _three_phase(r, s) is not None
                                          for s in ("generation", "consumption")))
    unknown = sum(1 for r in shown if r.get("three_phase_status") == "phases_unknown")
    rule = ("Kolumnerna generation_kw och consumption_kw är beräknade med en symmetrisk modell, där varje "
            "anslutning fördelas lika på de tre faserna. ")
    if not three_phase_shown(shown):
        return (rule + "Ingen trefaskontroll publiceras: ingen publicerad station har tillräckligt många kunder med "
                f"känd fas (minst {share} procent), eller så har kontrollen inte gjorts. Med enfasiga anslutningar "
                "kan den verkliga kapaciteten vara lägre än den symmetriska.")
    return (rule + "Kolumnerna three_phase_generation_kw och three_phase_consumption_kw gäller samma utbyggnad, "
            "kontrollerad med en osymmetrisk trefasmodell där varje kund ligger på sin registrerade fas (nya "
            "enfasiga växelriktare utan registrerad fas fördelas enligt programmets regel), varje timme vid båda "
            "ändarna av spänningsbandet för produktion och vid den lägre änden för förbrukning. Där trefasmodellen "
            "överskrider en gräns, även gränsen för spänningsosymmetri, minskas utbyggnaden tills den klarar "
            "varje timme; trefasvärdet är därför aldrig "
            f"högre än det symmetriska. {with_figure} av {len(shown)} publicerade stationer har ett trefasvärde, "
            f"varav {limited} är lägre än det symmetriska. {unknown} stationer saknar tillräckligt många kunder med "
            f"känd fas (minst {share} procent) och har inget värde i kolumnerna. En fas räknas som känd när "
            "anslutningen är registrerad på en fas, av nätföretaget eller identifierad från mätarnas spänningar mot "
            "transformatorns sekundärmätare, eller när den identifieringen bekräftar en trefasanslutning.")


def criteria_values(study: Study, q: str, rows: list[dict], result: dict, fixes: dict, state: dict,
                    published_at: str) -> dict[str, str]:
    """Every value grunder_for_berakning.sv.md takes, as Swedish text."""
    from .station import weather_source

    cfg = study.config
    code = code_version()
    device = DEVICE_SV[cfg.method.consumption_device]
    shown = [r for r in rows if result["stations"][r["station_id"]]["cell"]]
    published = [cid for cid, c in result["cells"].items() if not c["suppressed"]]
    if state["signed"]:
        s = state["signoff"]
        status = f"Signerad av {s['by']} den {s['signed_at'][:10]} (sigill {s['seal']['id']})."
    else:
        status = (f"**{DRAFT}**: inte signerad" + (" sedan underlaget ändrades efter signeringen" if state["signoff"]
                                                  else "") + f". Får inte publiceras som fastställd. {NOT_REVIEWED_SV}")
    weather = weather_source(study)
    weather_text = (f"från SMHI (STRÅNG och mätstationen {weather.get('station')}) för studiens plats"
                    if weather["source"] == "study" else
                    "referensnätets väderår, eftersom studien inte har eget väder för sin plats (syntetiskt här)")
    up_rows = [r for r in shown if r.get("generation_status") is not None]
    complete = sum(1 for r in up_rows if r.get("upstream_complete"))
    upstream = ("Nätföretagets uppgifter om ledig kapacitet i matande linjer, fördelningsstationer och "
                "regionnät tillämpas som tak: det publicerade värdet är det lägsta av stationens eget och "
                "varje nivås, och en kapacitet som flera stationer delar räknas inte ihop. När ett värde "
                "sätts av en nivå längre upp anger kolumnerna generation_shared_by_stations och "
                "consumption_shared_by_stations hur många stationer som delar på just den kapaciteten: "
                "värdena för de stationerna är samma kapacitet och får inte summeras. En nivå utan "
                f"uppgift redovisas som ej angiven och gissas aldrig. Fullständig kedja: {complete} av "
                f"{len(up_rows)} beräknade stationer.")
    if not (study.inputs / "upstream.csv").is_file():
        upstream = ("Nätföretaget har inte lämnat uppgifter om ledig kapacitet längre upp i nätet: värdena gäller "
                    "bara lågspänningsnätet och mellanspänningsbandet ovan.")
    ref = cfg.method.approval_reference.strip()
    if not ref:
        conditional = ("Villkorad kapacitet (anslutning med dynamiska gränser) publiceras inte: nätföretaget har "
                       "inte angett något godkännande av metoden.")
    else:
        n_ok = sum(1 for r in shown if _conditional(study, r) is not None)
        basis = ("summan för alla nya anläggningar" if cfg.method.budget == "aggregate"
                 else "varje enskild anläggning över alla väderår")
        conditional = (f"Villkorad kapacitet publiceras i en egen kolumn med stöd av godkännandet {ref}. Den gäller "
                       "anslutning med dynamiska gränser som har verifierats med lastflöde för varje timme, med högst "
                       f"{sv(_assumption(study, 'z_star_pct'))} procent begränsad produktion räknat på {basis}. "
                       f"{n_ok} av {len(shown)} publicerade stationer har ett verifierat värde; övriga saknar värde "
                       "i kolumnen.")
    three_phase = _three_phase_text(study, shown)
    n_fix = sum(len(v) for v in fixes.values())
    corrections_text = (f"Granskaren har gjort {n_fix} korrigeringar. En korrigering ändrar aldrig ett beräknat "
                        "värde: den redovisas i en egen kolumn bredvid och kan bara sänka ett värde eller dölja en "
                        "station." if n_fix else
                        "Inga korrigeringar: alla värden är de beräknade.")
    lvl = cfg.disclosure.level
    disclosure_text = (f"Värden publiceras per {LEVEL_SV[lvl]}"
                       + (f" (rutor om {cfg.disclosure.grid_m} meter i SWEREF 99 TM)" if lvl == "grid" else "")
                       + f". En cell med färre än {cfg.disclosure.min_customers} kunder slås samman uppåt"
                       + (f" (rutor om {cfg.disclosure.grid_m} meter, dubblade, därefter område och hela studien)"
                          if lvl != "zone" else " (till hela studien)")
                       + ". En sammanslagen cell visar det lägsta värdet bland sina stationer, aldrig en summa.")
    if cfg.disclosure.round_kw > 0 and cfg.disclosure.round_below_customers > 0:
        disclosure_text += (f" I en cell med färre än {cfg.disclosure.round_below_customers} kunder avrundas varje "
                            f"värde nedåt till närmaste {sv(cfg.disclosure.round_kw)} kW (kolumnen rounded_to_kw), "
                            "så att skillnaden mellan två publiceringar säger mindre om ett enskilt hushåll; "
                            f"värdet 0 betyder där mindre än {sv(cfg.disclosure.round_kw)} kW. "
                            "Skillnaden mellan två publiceringar kan ändå visa att något har anslutits i cellen.")
    crit = cfg.validation
    n_within = sum(1 for r in shown if r.get("validation_status") == "within")
    n_outside = sum(1 for r in shown if r.get("validation_status") == "outside")
    n_compared = sum(1 for r in shown if r.get("validation_status") in ("no_criterion", "within", "outside"))
    if crit.entered:
        limits = [f"{validation.CRITERION_SV[k]} högst {sv(v, 4)}" for k, v in
                  (("voltage_p95_pu", crit.voltage_p95_pu), ("transformer_p95_pct", crit.transformer_p95_pct))
                  if v is not None]
        if crit.min_metered_share is not None:
            limits.append(f"{validation.CRITERION_SV['min_metered_share']} minst {sv(100 * crit.min_metered_share, 0)} procent")
        validation_text = (f"Nätföretaget har angett godkännandekriterier ({crit.source}): {'; '.join(limits)}. "
                           f"{n_within} av {len(shown)} publicerade stationer är validerade mot mätningar inom dem. "
                           f"{n_outside} ligger utanför och har därför inget värde i publiceringen. Övriga saknar "
                           "mätvärden eller har inte jämförts.")
    else:
        validation_text = ("Nätföretaget har inte angett några godkännandekriterier för modellen mot mätningar. "
                           f"{n_compared} av {len(shown)} publicerade stationer har jämförts med mätningar "
                           "(se valideringsrapporten), men ingen station redovisas som validerad.")
    equipment = ("Befintlig utrustning: de solceller och annan utrustning som nätföretaget har registrerat räknas som "
                 "befintliga i alla timmar." if (study.inputs / "equipment.csv").is_file() else
                 "Befintlig utrustning: nätföretaget har inte lämnat något register över befintlig utrustning, så "
                 "ingen utrustning räknas som befintlig utöver nätet i sig.")
    meters = study.inputs / "meters"
    load_basis = ("Last: uppmätta timvärden för de kunder som har tillräckligt många mätvärden, annars syntetiska "
                  "profiler." if meters.is_dir() and any(meters.iterdir()) else
                  "Last: nätföretaget har inte lämnat några mätvärden, så lasten är syntetiska profiler (saknas "
                  "uppmätt underlag).")
    overrides = "\n".join(f"- {k}: {o.value} {o.unit} (källa: {o.source})" for k, o in cfg.assumptions.items()) \
        or "Inga: alla antaganden är programmets standardvärden."
    orders = sorted({int(r["orders"]) for r in rows if r.get("orders") is not None})
    pct = sorted({float(r["percentile"]) for r in rows if r.get("percentile") is not None}) or [cfg.method.percentile]
    return {
        "status": status, "dso_name": cfg.dso.name or study.id, "study_id": study.id, "quarter": q,
        "period_label": runs.label_sv(q),
        "published_at": published_at, "method_version": cfg.dso.method_version,
        "code_version": str(code.get("version")), "code_commit": (code.get("commit") or "okänd")[:12],
        "consumption_device": device, "consumption_device_capitalised": "En " + device,
        "stations_published": str(sum(len(result["cells"][c]["stations"]) for c in published)),
        "stations_total": str(len(rows)), "cells": str(len(published)),
        "stations_hidden": str(sum(1 for s in result["stations"].values() if s["hidden_by"])),
        "stations_suppressed": str(sum(1 for s in result["stations"].values() if s.get("suppressed"))),
        "weather_years": _years(rows), "weather_source": weather_text,
        "v_min_pu": sv(_assumption(study, "v_min_pu")), "v_max_pu": sv(_assumption(study, "v_max_pu")),
        "v_margin_pu": sv(_assumption(study, "v_margin_pu")),
        "thermal_limit_pct": sv(_assumption(study, "thermal_limit_pct")),
        "mv_low_pu": sv(cfg.mv.band_low_pu), "mv_high_pu": sv(cfg.mv.band_high_pu), "mv_source": cfg.mv.source_sv,
        "load_power_factor": sv(_assumption(study, "load_power_factor")),
        "orders": " eller ".join(str(o) for o in orders) or "–", "percentile": " eller ".join(sv(p) for p in pct),
        "upstream": upstream, "conditional": conditional, "three_phase": three_phase, "corrections": corrections_text,
        "disclosure": disclosure_text, "validation": validation_text, "overrides": overrides,
        "equipment": equipment, "load_basis": load_basis,
    }


def render(template: str, values: dict[str, str]) -> str:
    """The template with every {{placeholder}} filled; one without a value raises KeyError."""
    missing = sorted({m for m in PLACEHOLDER.findall(template) if m not in values})
    if missing:
        raise KeyError(f"no value for {', '.join(missing)}")
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def _stats(block: dict | None, scale: float = 1.0, decimals: int = 3) -> str:
    if not block or not block.get("n"):
        return "inga jämförbara värden"
    return (f"medel {sv(block['bias'] * scale, decimals)}, MAE {sv(block['mae'] * scale, decimals)}, "
            f"RMSE {sv(block['rmse'] * scale, decimals)}, p95 {sv(block['p95'] * scale, decimals)}, "
            f"max {sv(block['max'] * scale, decimals)} (n = {block['n']})")


def validation_report(study: Study, q: str, rows: list[dict], result: dict, status_line: str) -> str:
    """validation_report.sv.md: each station published on its own, against the DSO's measurements
    (study validate); stations in merged cells are only counted."""
    lines = ["# Valideringsrapport", "", status_line, "",
             f"Studie {study.id}, {runs.word_sv(q)} {q}, metodversion {study.config.dso.method_version}.", "",
             "Modellen jämförs med nätföretagets mätningar: uppmätta timmar spelas upp i modellen vid uppmätt "
             "mellanspänning, eller vid båda ändarna av spänningsbandet, och avvikelsen redovisas som modell minus "
             "mätning. Kunder redovisas aldrig enskilt.", ""]
    own = [r for r in rows if (s := result["stations"][r["station_id"]])["cell"] and s.get("level") == "station"
           and not s.get("merged") and not s.get("suppressed")]
    own_ids = {r["station_id"] for r in own}
    merged = sum(1 for sid, s in result["stations"].items() if s["cell"] and sid not in own_ids)
    crit = study.config.validation
    counts = {s: 0 for s in validation.STATUSES}
    for r in own:
        lines += [f"## {r['station_id']} – {r.get('name') or r['station_id']}", ""]
        v = validation.read(study, r["station_id"])
        verdict = validation.verdict(v, crit, r.get("customers"))
        counts[verdict["status"]] += 1
        lines.append(f"- Bedömning: {validation.SV[verdict['status']]}.")
        for c in verdict["checks"]:
            how = "högst" if c["criterion"] != "min_metered_share" else "minst"
            scale, decimals = (100.0, 0) if c["criterion"] == "min_metered_share" else (1.0, 4)
            value = "mätvärde saknas" if c["value"] is None else sv(c["value"] * scale, decimals)
            where = f", {validation.BASIS_SV[c['basis']]}" if c["basis"] else ""
            lines.append(f"- Kriterium, {validation.CRITERION_SV[c['criterion']]}{where}: {value} "
                         f"({how} {sv(c['limit'] * scale, decimals)}): "
                         f"{'uppfyllt' if c['ok'] else 'ej bedömt' if c['ok'] is None else 'ej uppfyllt'}.")
        if v is None:
            lines += ["- Ingen validering körd (`kapacitet study validate`).", ""]
            continue
        m = v.get("measured") or {}
        if m.get("bases"):
            lines.append(f"- Uppmätta timmar: {m.get('hours_complete')}, mätta kunder: {m.get('metered_customers')}, "
                         f"mellanspänning: {'uppmätt' if m.get('mv') == 'measured' else 'bandets ändar'}.")
            for name, b in m["bases"].items():
                lines.append(f"- Spänning ({name}, p.u.): {_stats(b.get('voltage_pu'), decimals=4)}.")
                lines.append(f"- Transformatorbelastning ({name}, procentenheter): "
                             f"{_stats(b.get('transformer_pct'), decimals=1)}.")
        else:
            lines.append("- Mätvärden: inga.")
        snap = v.get("dppower") or {}
        if snap:
            lines.append(f"- dpPower-ögonblicksbild: {len(snap)} timmar jämförda element för element.")
        cal = v.get("calibrate_static") or {}
        if cal:
            lines.append(f"- Nätföretagets statiska resultat: {cal.get('matched')} av {cal.get('rows')} kunder matchade.")
            for col, label in (("gen_kw", "produktion"), ("load_kw", "förbrukning")):
                if col in cal:
                    lines.append(f"- Statisk {label} (modell minus nätföretaget, kW): {_stats(cal[col], decimals=1)}.")
        lines.append("")
    compared = counts["no_criterion"] + counts["within"] + counts["outside"]
    lines += ["## Sammanfattning", "", f"- Stationer som redovisas var för sig: {len(own)}.",
              f"- Jämförda med mätningar: {compared}.",
              (f"- Validerade, inom nätföretagets toleranser ({crit.source}): {counts['within']}." if crit.entered else
               "- Validerade: 0. Nätföretaget har inte angett några godkännandekriterier, så ingen station "
               "redovisas som validerad."),
              f"- Utanför toleranserna, publiceras inte: {counts['outside']}.",
              f"- Utan mätvärden eller utan körd validering: {counts['not_measured'] + counts['not_validated']}.",
              f"- Stationer i sammanslagna celler, som inte redovisas var för sig: {merged}.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- publish

def publish(study: Study, q: str) -> dict:
    """runs/<q>/publication/ (see the module's docstring). Returns {"folder", "status", "files",
    "changed", "cells", "findings"}."""
    rows = load_rows(study, q)
    check_method(study, rows)
    fixes, findings = corrections(study, q, rows)
    cfg = study.config
    result = disclosure.assign(rows, cfg.disclosure, study.id, fixes)
    state = signoff_state(study, q, rows)
    # after the signature's check, which reads the files: a station outside the DSO's validation
    # tolerances loses its figures here, in every file of the publication
    rows, verdicts = judged(study, rows)
    status = SIGNED if state["signed"] else DRAFT
    published_at = dt.datetime.now(dt.timezone.utc).date().isoformat()
    folder = run_dir(study, q)
    tmp = folder / "publication.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    table = cells(study, q, rows, fixes, result, status)
    shown = [r for r in rows if result["stations"][r["station_id"]]["cell"]]
    columns = [c for c in CAPACITY_COLUMNS
               if (c != "conditional_generation_kw" or cfg.method.approval_reference.strip())
               and (c not in THREE_PHASE_COLUMNS or three_phase_shown(shown))]
    csvio.write(tmp, "capacity", table, columns, cfg.output.csv_dialect)
    meta = {"status": status, "study": study.id, "dso": cfg.dso.name, "quarter": q,
            "method_version": cfg.dso.method_version}
    write_json_atomic(tmp / "capacity.geojson", geojson(table, result, meta), ensure_ascii=False)
    values = criteria_values(study, q, rows, result, fixes, state, published_at)
    write_text_atomic(tmp / "grunder_for_berakning.md", render(TEMPLATE.read_text(encoding="utf-8"), values))
    write_text_atomic(tmp / "validation_report.sv.md", validation_report(study, q, rows, result, values["status"]))
    signoff = state["signoff"]
    embed.write(tmp, table, columns, {**meta, "published_at": published_at,
                                      "signoff": {"by": signoff["by"], "valid": state["signed"]} if signoff else None},
                cfg.publication)
    files = {p.relative_to(tmp).as_posix(): {"sha256": file_digest(p), "bytes": p.stat().st_size}
             for p in sorted(tmp.rglob("*")) if p.is_file()}
    manifest = {
        "schema": MANIFEST_SCHEMA, "status": status, "study": study.id, "dso": cfg.dso.name, "quarter": q,
        "published_at": published_at, "method_version": cfg.dso.method_version, "code": code_version(),
        "signoff": ({"by": signoff["by"], "signed_at": signoff["signed_at"], "seal": signoff.get("seal"),
                     "valid": state["signed"], "changed_since": state["changed"]} if signoff else None),
        "disclosure": {"level": result["level"], "min_customers": result["min_customers"], "grid_m": result["grid_m"],
                       "round_kw": cfg.disclosure.round_kw, "round_below_customers": cfg.disclosure.round_below_customers,
                       "cells": len([c for c in result["cells"].values() if not c["suppressed"]]),
                       "suppressed_cells": len([c for c in result["cells"].values() if c["suppressed"]]),
                       "stations_hidden": sum(1 for s in result["stations"].values() if s["hidden_by"])},
        "conditional_column": bool(cfg.method.approval_reference.strip()),
        "three_phase_columns": three_phase_shown(shown),
        # the DSO's validation criteria and how many stations stand where against them: counts only,
        # never which station
        "validation": {"criteria": cfg.validation.model_dump() if cfg.validation.entered else None,
                       "stations": {s: sum(1 for v in verdicts.values() if v["status"] == s)
                                    for s in validation.STATUSES}},
        "corrections": sum(len(v) for v in fixes.values()), "correction_findings": len(findings),
        "outputs": public_outputs(study, q, rows), "files": files,
        "period": {"kind": runs.kind(q), "first_day": runs.period(q)[0].isoformat(),
                   "last_day": runs.period(q)[1].isoformat()},
        "embed": embed.manifest_block(cfg.publication),
        # the public key that checks the signoff's signed seal, and how (never the private key)
        "seal_key": _seal_key(signoff),
    }
    write_json_atomic(tmp / "manifest.json", manifest, ensure_ascii=False)
    final = folder / "publication"
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    csvio.write(folder, "disclosure_report", disclosure.report(rows, result), disclosure.REPORT_COLUMNS,
                cfg.output.csv_dialect)
    return {"folder": final, "status": status, "files": sorted(files), "changed": state["changed"],
            "cells": len(table), "findings": findings}


__all__ = ["CAPACITY_COLUMNS", "DRAFT", "SIGNED", "TEMPLATE", "check_method", "criteria_values", "outputs",
           "publish", "render", "sign", "signoff_state", "sv", "validation_report"]
