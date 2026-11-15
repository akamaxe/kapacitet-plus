"""The report a DSO files: a project's capacity, or a study's run, as a Swedish DOCX (task 2.6).

Every figure is read from the files the engine and the study wrote; nothing is computed here. A
project's report states its input data and their quality, the method and its criteria (the DSO's
static method profile, method_profile.py), the results of the static and the hourly study for
generation and consumption, the validation, the version that computed them and the note that no
native speaker has reviewed the Swedish. A run's report states each station's published figures,
its validation, and who signed the run and when.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..language import NOT_REVIEWED_SV
from .docx import Document

RULES_SV = {"min_daytime_summer": "varje hushålls lägsta last dagtid på sommaren (maj–augusti, 10–16)",
            "no_load": "ingen hushållslast",
            "fixed_per_customer": "en fast last per kund, nätbolagets egen siffra"}


def sv(x, decimals: int = 0) -> str:
    """A number as Swedish text (study/publish.sv): a decimal comma, trailing zeros dropped."""
    from ..study.publish import sv as publish_sv

    return publish_sv(x, decimals)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def project_report(data_dir: Path | None = None) -> Document:
    """The report of the project whose data folder is `data_dir` (the one in use by default)."""
    from .. import __version__, method_profile, paths
    from ..study.config import METHOD_VERSION

    base = Path(data_dir or paths.DATA) / "processed"
    grid = _read(base / "grid" / "summary.json")
    meta = _read(base / "results" / "meta.json")
    hosting = _read(base / "results" / "hosting_summary.json")
    consumption = _read(base / "results" / "consumption.json")
    validation = _read(base / "results" / "validation.json")
    site = _read(base / "site.json")
    name = grid.get("site") or meta.get("site") or "Projekt"
    computed = str(meta.get("computed_at") or "–")[:19].replace("T", " ")
    doc = Document(f"Kapacitetsrapport – {name}", header=f"Kapacitet+ · {name} · beräknad {computed}",
                   footer=f"Metod {METHOD_VERSION} · Kapacitet+ {__version__} · {NOT_REVIEWED_SV}")

    doc.heading("Indata och kvalitet")
    years = meta.get("years") or []
    doc.table(["Uppgift", "Värde"], [
        ["Kunder", sv(grid.get("customers"))], ["Noder", sv(grid.get("buses"))], ["Ledningar", sv(grid.get("lines"))],
        ["Transformator", grid.get("trafo_type") or "–"],
        ["Väderår", f"{min(years)}–{max(years)}" if years else "–"],
        ["Adoptionsordningar", sv(hosting.get("n_orders"))],
    ])
    if site:
        st = site.get("station") or {}
        doc.paragraph(f"Väder från SMHI STRÅNG vid {sv(site.get('lat'), 3)}, {sv(site.get('lon'), 3)} och "
                      f"temperatur från station {st.get('name', '–')}; priser för elområde "
                      f"{(site.get('prices') or {}).get('zone', '–')}.")
    else:
        doc.paragraph("Väder och priser från referensplatsen.")
    doc.paragraph("Hushållens last kommer från den syntetiska reserven av hushållsprofiler, utom för kunder med "
                  "mätaravläsningar, och lågspänningsnätets data från nätexporten eller referensnätet.", italic=True)

    doc.heading("Metod och kriterier")
    static = hosting.get("static_definition") or {}
    rule = static.get("load_rule", "min_daytime_summer")
    profile = method_profile.load(Path(data_dir or paths.DATA))
    rows = [["Solcellseffekt i den statiska ögonblicksbilden", f"{sv(static.get('pv_factor'), 2)} × AC-märkeffekt"],
            ["Hushållslast i den statiska ögonblicksbilden", RULES_SV.get(rule, rule)],
            ["Spänning på mellanspänningssidan", f"{sv(static.get('mv_voltage_pu'), 2)} pu"],
            ["Högsta belastning", f"{sv(static.get('max_loading_pct', 100), 0)} %"],
            ["Största spänningshöjning i anslutningspunkten",
             f"{sv(static.get('v_rise_limit_pct'), 1)} %" if static.get("v_rise_limit_pct") else "inget eget krav"]]
    doc.table(["Kriterium", "Värde"], rows)
    doc.paragraph(f"Den statiska metoden angavs av: {profile.source}." if profile.source.strip()
                  else "Den statiska metoden är standardprofilen: dagens statiska studie.")
    doc.paragraph("Timstudien kör varje timme i varje väderår med fullständig lastflödesberäkning och utan styrning; "
                  "en anslutning den godkänner är fast.")

    doc.heading("Resultat")
    m = hosting.get("measures") or {}

    def spread(key: str) -> list[str]:
        kw = (m.get(key) or {}).get("kw_ac") or {}
        return [sv(kw.get("median")), f"{sv(kw.get('p10'))}–{sv(kw.get('p90'))}"]

    devices = consumption.get("devices") or {}

    def adopters(key: str) -> str:
        return sv(((devices.get(key) or {}).get("adopters") or {}).get("median"), 1)

    doc.table(["Mått", "Median", "P10–P90"], [
        ["Ny solcellseffekt, statisk studie (kW)", *spread("static")],
        ["Ny solcellseffekt, timstudie (kW)", *spread("timeseries")],
    ])
    doc.table(["Förbrukning", "Antal enligt timstudien", "Statisk konvention"], [
        ["Laddare för elbil", adopters("ev"), adopters("flat")],
        ["Värmepumpar", adopters("hp"), "–"],
    ])
    if consumption.get("basis"):
        doc.paragraph(f"Förbrukningen begränsas av: {consumption['basis']}.", italic=True)

    doc.heading("Validering")
    shape = validation.get("household_shape") or {}
    if shape.get("corr_hour_of_day") is not None:
        doc.paragraph(f"Hushållslastens dygnsprofil mot eSetts uppmätta aggregat {shape.get('year', '')}: "
                      f"korrelation {sv(shape['corr_hour_of_day'], 3)}.")
    else:
        doc.paragraph("Ingen validering mot uppmätta data finns för projektet.")

    doc.heading("Version och signatur")
    versions = meta.get("versions") or {}
    doc.table(["Uppgift", "Värde"], [
        ["Beräknad", computed], ["Metodversion", METHOD_VERSION], ["Kapacitet+", __version__],
        ["Programbibliotek", ", ".join(f"{k} {v}" for k, v in sorted(versions.items())) or "–"],
    ])
    doc.paragraph("Rapporten är inte signerad: en studies körning signeras med `kapacitet study sign`.")
    doc.paragraph(NOT_REVIEWED_SV, italic=True)
    return doc


def study_report(study, run: str) -> Document:
    """The report of a study's run: each station's published figures, the run's signature."""
    import pandas as pd

    from .. import __version__
    from ..study.config import METHOD_VERSION

    folder = study.runs / run
    stations = folder / "stations.parquet"
    if not stations.is_file():
        from ..study import StudyError

        raise StudyError("not_collected", quarter=run, path=str(study.root))
    rows = pd.read_parquet(stations).to_dict("records")
    signoff = _read(folder / "signoff.json")
    manifest = _read(folder / "publication" / "manifest.json")
    name = study.config.dso.name or study.id
    doc = Document(f"Kapacitetsstudie {run} – {name}", header=f"Kapacitet+ · {name} · körning {run}",
                   footer=f"Metod {study.config.dso.method_version} · Kapacitet+ {__version__} · {NOT_REVIEWED_SV}")
    doc.heading("Nätstationer")
    doc.paragraph(f"{sv(len(rows))} nätstationer, {sv(sum(int(r.get('customers') or 0) for r in rows))} kunder.")
    doc.table(["Nätstation", "Kunder", "Produktion (kW)", "Förbrukning (kW)", "Begränsar", "Validering"], [
        [r.get("name") or r["station_id"], sv(r.get("customers")), sv(r.get("published_generation_kw")),
         sv(r.get("published_consumption_kw")), r.get("generation_limit") or "–", r.get("validation_status") or "–"]
        for r in rows])
    doc.heading("Metod")
    doc.paragraph(f"Metodversion {study.config.dso.method_version} (programmet räknar med {METHOD_VERSION}); "
                  f"percentil {sv(study.config.method.percentile)}; banden på mellanspänningssidan "
                  f"{sv(study.config.mv.band_low_pu, 2)}–{sv(study.config.mv.band_high_pu, 2)} pu.")
    doc.heading("Signatur")
    if signoff:
        doc.paragraph(f"Signerad av {signoff.get('by', '–')} {str(signoff.get('signed_at', ''))[:19].replace('T', ' ')}; "
                      f"förseglad som {((signoff.get('seal') or {}).get('id')) or '–'} i studiens beviskedja.")
    else:
        doc.paragraph("Körningen är inte signerad.")
    doc.paragraph(f"Publiceringen: {manifest.get('status', 'inte publicerad')}.")
    doc.paragraph(NOT_REVIEWED_SV, italic=True)
    return doc
