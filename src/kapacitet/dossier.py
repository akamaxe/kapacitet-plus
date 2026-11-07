"""The method dossier for Ei: how the figures are computed, as a Swedish text a DSO can hand in.

    kapacitet method-dossier                 the demo: data/processed/results/method_dossier_sv.md
    kapacitet method-dossier --study <dir>   a study: <dir>/method_dossier_sv.md

Every DSO needs its method for conditional agreements approved by Ei before it signs one
(Ei2025:01, as config/ei_conditions.toml cites it). The dossier describes that method from what
this installation holds: the code (its constants and defaults), config/assumptions.toml (with a
study's overrides), the published results, the DSO's settings and records, the seal chain and the
download manifest. Nothing in it is typed by hand: each figure is a fact, collected with its
source into `method_dossier.json`, and the Swedish text only formats facts
(`tests/test_dossier.py` checks that every number in the text is one of them). The template,
templates/method_dossier.sv.md, holds no figure at all.

A result that is missing is said to be missing, with the command that writes it; it is never
filled in. Like the Ei pack (engine/eipack.py) the dossier gives evidence, never a legal
conclusion: it names Ei's sections only where config/ei_conditions.toml already does.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .paths import CONFIG

TEMPLATE = Path(__file__).with_name("templates") / "method_dossier.sv.md"
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
SCHEMA = "kapacitet.method-dossier/1"
TEXT_NAME = "method_dossier_sv.md"
JSON_NAME = "method_dossier.json"
# a number as the text writes it: digits, thousands after a narrow no-break space, a decimal comma
NUMBER = re.compile(r"\d+(?: \d{3})*(?:,\d+)?")

COMPUTE = "python -m kapacitet compute"
EXTRA = "python -m kapacitet compute --stage extra --only "
WRITTEN_BY = {
    "meta.json": COMPUTE,
    "hosting_summary.json": COMPUTE,
    "operation/index.json": EXTRA + "operation",
    "foresight.json": EXTRA + "foresight",
    "intraday.json": EXTRA + "intraday",
    "redispatch.json": "python -m kapacitet redispatch-report",
    "mv_band.json": EXTRA + "mvband",
}

MONTHS_SV = ("januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober",
             "november", "december")
POLICY_SV = {"maxmin": "maxmin", "prorata": "proportionell", "propfair": "proportionellt rättvis",
             "maxtotal": "maxtotal"}
POLICY_TEXT_SV = {
    "maxmin": "lexikografisk max-min-rättvisa: varje villkorad anläggning får samma gräns i kW, utom när den "
              "behöver mindre eller när en gräns som den påverkar binder",
    "prorata": "samma fördelning viktad med växelriktarens märkeffekt: alla begränsas till samma andel av sin "
               "märkeffekt",
    "propfair": "proportionell rättvisa: summan av logaritmen av varje anläggnings exporterade energi maximeras",
    "maxtotal": "den sammanlagda exporten maximeras; regeln visar orättvisan mot kunder längst ut på ledningen",
}
BASIS_SV = {"foresight": "perfekt framförhållning (planeringssiffra)", "dayahead": "utfärdad dagen före"}
KIND_SV = {"local_flexibility_market": "lokal flexibilitetsmarknad", "bilateral_flexibility": "bilateral flexibilitet",
           "network_tariff": "nättariff", "none_available": "inget tillgängligt", "other": "annat"}
RESULT_SV = {"pass": "uppfyllt", "fail": "inte uppfyllt"}
DEVICE_SV = {"ev": "elbilsladdare", "heat_pump": "värmepump"}


class DossierError(ValueError):
    pass


# ---------------------------------------------------------------- facts and their Swedish text

def sv_number(x: float | int | None, decimals: int = 0, fixed: bool = False) -> str:
    """A number as Swedish text: a decimal comma, trailing zeros dropped (kept when `fixed`, as for
    a voltage in p.u.), and a narrow no-break space between thousands from five digits on
    (docs/glossary.md)."""
    if x is None:
        return "–"
    v = float(x)
    whole, _, frac = f"{abs(v):,.{decimals}f}".partition(".")
    digits = whole.replace(",", "")
    whole = whole.replace(",", " ") if len(digits) > 4 else digits
    frac = frac if fixed else frac.rstrip("0")
    text = whole + ("," + frac if frac else "")
    return ("−" if v < 0 and text.strip("0, ") else "") + text


class Facts:
    """Every value the dossier cites, with its source; the text is made from these only."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.missing: list[dict] = []

    def _put(self, key: str, value: Any, text: str, source: str, **extra) -> str:
        if key in self.items and self.items[key]["text"] != text:
            raise DossierError(f"fact {key} given twice with different values")
        self.items[key] = {"value": value, "text": text, "source": source, **extra}
        return text

    def num(self, key: str, value: float | int | None, source: str, decimals: int = 0, unit: str = "",
            fixed: bool = False) -> str:
        v = None if value is None else (int(value) if decimals == 0 and float(value).is_integer() else float(value))
        return self._put(key, v, sv_number(value, decimals, fixed), source, decimals=decimals, fixed=fixed, unit=unit)

    def text(self, key: str, value: Any, source: str, text: str | None = None) -> str:
        return self._put(key, value, str(value) if text is None else text, source)

    def years(self, key: str, years: list[int], source: str) -> str:
        ys = sorted({int(y) for y in years})
        if not ys:
            return self.text(key, [], source, "–")
        run = ys == list(range(ys[0], ys[-1] + 1)) and len(ys) > 2
        return self.text(key, ys, source, f"{ys[0]}–{ys[-1]}" if run else ", ".join(str(y) for y in ys))

    def lack(self, name: str) -> str:
        """A result that is not there: recorded, and said in the text with the command that writes it."""
        cmd = WRITTEN_BY.get(name, COMPUTE)
        if not any(m["file"] == name for m in self.missing):
            self.missing.append({"file": name, "command": cmd})
        return f"*Saknas:* `{name}` är inte beräknad. Den skrivs av `{cmd}`."


def _read(folder: Path, name: str) -> dict | None:
    try:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ei(f: Facts) -> dict:
    """Ei's conditions as config/ei_conditions.toml writes them, each section a fact."""
    with open(CONFIG / "ei_conditions.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    src = "config/ei_conditions.toml"
    title = cfg["source"]["title"]
    doc = re.search(r"Ei\d{4}:\d{2}", title)
    f.text("ei.document", doc.group(0) if doc else title, src)
    f.text("ei.title", title, src)
    f.text("ei.url", cfg["source"]["url"], src)
    out = {}
    for c in cfg["condition"]:
        out[c["key"]] = {"source": f.text(f"ei.{c['key']}.source", c["source"], src),
                         "label": f.text(f"ei.{c['key']}.label_sv", c["label_sv"], src)}
    return out


def _cite(ei: dict, key: str) -> str:
    return f"«{ei[key]['label']}» ({ei[key]['source']})"


def _assumption(f: Facts, key: str, decimals: int = 2, overrides: dict | None = None, sep: str = "–",
                fixed: bool = False) -> str:
    from .config import assumptions

    a = assumptions()[key]
    over = (overrides or {}).get(key)
    value = over.value if over is not None else a.value
    source = (f"study.toml [assumptions] {key} ({over.source})" if over is not None
              else f"config/assumptions.toml [{key}]: {a.source}" + (f" {a.url}" if a.url else ""))
    if isinstance(value, list):
        return f.text(f"assumption.{key}", value, source, sep.join(sv_number(v, decimals, fixed) for v in value))
    if isinstance(value, str):
        return f.text(f"assumption.{key}", value, source)
    return f.num(f"assumption.{key}", value, source, decimals, a.unit, fixed)


def _median(values: list[float]) -> float | None:
    v = sorted(float(x) for x in values if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


# ---------------------------------------------------------------- the sections, for the demo

def _computed_demo(f: Facts, res: Path) -> str:
    h = _read(res, "hosting_summary.json")
    if h is None:
        return f.lack("hosting_summary.json")
    src = "results/hosting_summary.json"
    d = h.get("static_definition") or {}
    months = d.get("months") or []
    hours = d.get("hours") or []
    mtext = f.text("static.months", months, src + ": static_definition.months",
                   "–".join(MONTHS_SV[int(m) - 1] for m in months)) if months else "–"
    htext = f.text("static.hours", hours, src + ": static_definition.hours (svensk normaltid)",
                   "–".join(str(int(x)) for x in hours)) if hours else "–"
    m = h.get("measures") or {}

    def med(name: str) -> str:
        v = ((m.get(name) or {}).get("kw_ac") or {}).get("median")
        return f.num(f"capacity.{name}_kw", v, f"{src}: measures.{name}.kw_ac.median", 0, "kW AC")

    orders = f.num("capacity.orders", h.get("n_orders"), src + ": n_orders")
    years = f.years("capacity.years", h.get("years") or [], src + ": years")
    n_years = f.num("capacity.n_years", len(h.get("years") or []), src + ": years")
    customers = f.num("capacity.customers", h.get("customers"), src + ": customers")
    z = f.num("capacity.z_star_pct", h.get("z_star_pct"), src + ": z_star_pct", 1, "% av årlig solel")
    return "\n".join([
        f"Nätet har {customers} kunder. Varje kapacitet räknas för {orders} slumpade anslutningsordningar och "
        f"väderåren {years} ({n_years} år); värdet är medianen över ordningarna.",
        "",
        "| Beräkning | Vad den kontrollerar | Median |",
        "|---|---|---|",
        f"| Statisk gräns (värsta fall) | ett sommarläge: full solel mot varje hus lägsta last dagtid "
        f"({mtext}, kl. {htext}) under något väderår | {med('static')} kW |",
        f"| Timvis studie | varje timme av varje väderår, utan dynamiska gränser | {med('timeseries')} kW |",
        f"| Med dynamiska gränser, summerad budget | varje timme, med högst {z} % nedreglerad solel räknat på "
        f"summan för alla villkorade anläggningar | {med('doe')} kW |",
        f"| Med dynamiska gränser, budget per anläggning | samma, med budgeten för varje anläggning för sig | "
        f"{med('doe_installation')} kW |",
    ])


def _allocation_demo(f: Facts, res: Path, ei: dict) -> str:
    from . import dso
    from .engine.operation import BASES, POLICIES

    rule = dso.load().allocation_policy
    lines = [f"- **{POLICY_SV[p]}** (`{p}`): {POLICY_TEXT_SV[p]}." for p in POLICIES]
    lines += ["", f"Nätbolagets regel är **{f.text('dso.allocation_policy', POLICY_SV[rule], 'DSO settings: allocation_policy')}**"
              " (nätbolagets inställningar, sigillerade vid varje ändring). Villkoren ska ange hur och när "
              f"överföringen får begränsas: {_cite(ei, 'terms_how_when')}.", ""]
    idx = _read(res, "operation/index.json")
    if idx is None:
        return "\n".join(lines + [f.lack("operation/index.json")])
    src = "results/operation/index.json"
    lines += [f"Varje kombination av grund och regel räknas för året "
              f"{f.text('operation.year', idx.get('year'), src + ': year')} och verifieras över hela året:", "",
              "| Grund | Regel | Verifierad | Timmar med överträdelse | Begränsade timmar | Nedreglerad energi |",
              "|---|---|---|---|---|---|"]
    for b in BASES:
        for p in POLICIES:
            c = (idx.get("combos") or {}).get(b, {}).get(p)
            if not c:
                continue
            at = f"{src}: combos.{b}.{p}"
            lines.append(f"| {BASIS_SV[b]} | {POLICY_SV[p]} | {'ja' if c.get('verified') else 'nej'} | "
                         f"{f.num(f'operation.{b}.{p}.violation_hours', c.get('full_year_violation_hours'), at + '.full_year_violation_hours')} | "
                         f"{f.num(f'operation.{b}.{p}.hours_limited', c.get('hours_limited'), at + '.hours_limited')} | "
                         f"{f.num(f'operation.{b}.{p}.curtailed_kwh', c.get('curtailed_kwh'), at + '.curtailed_kwh', 0, 'kWh')} kWh |")
    return "\n".join(lines)


def _verification(f: Facts, res: Path | None, overrides: dict | None = None) -> str:
    from .engine import netmodel

    code = "kapacitet/engine/netmodel.py"
    vmax = _assumption(f, "v_max_pu", 2, overrides, fixed=True)
    vmin = _assumption(f, "v_min_pu", 2, overrides, fixed=True)
    marg = _assumption(f, "v_margin_pu", 3, overrides)
    therm = _assumption(f, "thermal_limit_pct", 1, overrides)
    exp = f.num("code.export_screen_v_pu", netmodel.EXPORT_SCREEN_V, code + ": EXPORT_SCREEN_V", 3, "pu")
    imp = f.num("code.import_screen_v_pu", netmodel.IMPORT_SCREEN_V, code + ": IMPORT_SCREEN_V", 3, "pu")
    text = [
        f"Gränserna är spänning mellan {vmin} och {vmax} p.u. hos varje kund, med en marginal på {marg} p.u., och "
        f"belastning på högst {therm} procent av märkvärdet för kablar och transformator.",
        "",
        "1. **Linjärt filter.** Ett linjärt filter väljer ut de timmar som kan bryta en gräns. Det har en egen "
        f"spänningsmarginal ({exp} p.u. vid export, {imp} p.u. vid import), bortser bara från termer som skulle "
        "sänka uppskattningen och använder känsligheter som är avkortade vid noll. Filtret kan därför inte hoppa "
        "över en timme med överträdelse; det kan bara ta med för många.",
        "2. **Fördelning och åtstramning.** Exportgränserna fördelas med en linjär modell. Marginalerna stramas åt "
        "mot den fullständiga icke-linjära lastflödesberäkningen, och varje timme backas med bisektion tills ingen "
        "överträdelse återstår.",
        "3. **Kontroll av hela året.** Varje timme då en villkorad kund exporterar löses med fullständig "
        "icke-linjär lastflödesberäkning med de publicerade gränserna. Programmet vägrar publicera eller använda "
        "exportgränser som har en enda timme med överträdelse, en timme utan konvergerad lastflödesberäkning eller "
        "som inte har kontrollerats alls (`doe.require_verified`).",
    ]
    if res is not None:
        idx = _read(res, "operation/index.json")
        if idx is not None:
            combos = [c for b in (idx.get("combos") or {}).values() for c in b.values()]
            src = "results/operation/index.json: combos"
            ok = f.num("verification.combos_verified", sum(1 for c in combos if c.get("verified")), src + " (verified)")
            n = f.num("verification.combos", len(combos), src)
            viol = f.num("verification.violation_hours", sum(int(c.get("full_year_violation_hours") or 0) for c in combos),
                         src + " (full_year_violation_hours, summed)")
            text += ["", f"I de publicerade resultaten är {ok} av {n} kombinationer verifierade, med sammanlagt {viol} "
                         "timmar med överträdelse."]
    return "\n".join(text)


def _budget_demo(f: Facts, res: Path) -> str:
    from . import dso

    z = _assumption(f, "z_star_pct", 1)
    h = _read(res, "hosting_summary.json")
    agreed = f.num("dso.agreed_level_pct", dso.load().agreed_level_pct, "DSO settings: agreed_level_pct", 1, "%")
    text = [f"Budgeten är högst {z} % av en anläggnings årliga solel. Den kan räknas på två sätt:", "",
            "- **Summerad:** andelen av den sammanlagda solelen hos alla villkorade anläggningar.",
            "- **Per anläggning:** varje anläggnings egen andel över alla väderår. Det sämsta enskilda året redovisas "
            "men styr inte.", "",
            f"Det som ett avtal utlovar är nätbolagets nivå, {agreed} % (nätbolagets inställningar)."]
    if h is None:
        return "\n".join(text + ["", f.lack("hosting_summary.json")])
    src = "results/hosting_summary.json"
    per = h.get("per_order") or []
    basis = h.get("budget_basis") or {}
    f.text("budget.basis_headline", basis.get("doe"), src + ": budget_basis.doe")
    z_med = f.num("budget.aggregate_z_median_pct", _median([o.get("z_doe_pct") for o in per]),
                  src + ": per_order[].z_doe_pct (median)", 2, "%")
    worst = f.num("budget.aggregate_customer_max_pct", max((o.get("z_doe_customer_max_pct") or 0) for o in per) if per else None,
                  src + ": per_order[].z_doe_customer_max_pct (max)", 1, "%")
    inst = [o.get("z_doe_installation") or {} for o in per]
    i_max = f.num("budget.installation_customer_max_pct", max((i.get("customer_max_pct") or 0) for i in inst) if inst else None,
                  src + ": per_order[].z_doe_installation.customer_max_pct (max)", 1, "%")
    i_year = f.num("budget.installation_customer_year_max_pct",
                   max((i.get("customer_year_max_pct") or 0) for i in inst) if inst else None,
                   src + ": per_order[].z_doe_installation.customer_year_max_pct (max)", 1, "%")
    head = basis.get("doe")
    text += ["", {"aggregate": "Huvudresultatet räknas med summerad budget.",
                  "installation": "Huvudresultatet räknas med budget per anläggning."}.get(
                      head, "Det publicerade resultatet anger inte vilken budget huvudresultatet räknas med.")]
    return "\n".join(text + [
        "",
        f"Med summerad budget är den nedreglerade andelen i median {z_med} %, men en enskild anläggning kan få upp "
        f"till {worst} %. Med budget per anläggning är den högsta andelen för en anläggning över väderåren {i_max} %; "
        f"det sämsta enskilda året når {i_year} % och redovisas bara.",
    ])


def _dayahead(f: Facts, res: Path, ei: dict) -> str:
    pv = _assumption(f, "dayahead_pv_margin_pct", 1)
    load = _assumption(f, "dayahead_load_margin_pct", 1)
    text = [
        "En exportgräns som utfärdas dagen före planeras mot ett läge som dominerar det verkliga: timme för timme "
        "minst lika mycket solel och högst lika mycket last. Då beror säkerheten inte på att prognosen träffar, inte "
        "heller under de timmar som planeringen inte markerade som kritiska. Läget byggs av de klimatologiska "
        "ytterligheterna under samma period av året i alla väderår, med en marginal på "
        f"{pv} % över solelsgränsen och {load} % under lastgolvet.",
        "",
        "Bara grunden dagen före utfärdas till växelriktaren. Perfekt framförhållning är planeringssiffran och "
        "utfärdas aldrig.",
    ]
    fs = _read(res, "foresight.json")
    if fs is None:
        text += ["", f.lack("foresight.json")]
    else:
        src = "results/foresight.json"
        e = fs.get("exceedance") or {}
        win = f.num("dayahead.window_days", fs.get("window_days") or e.get("window_days"), src + ": window_days", 0, "dagar")
        if (fs.get("median_kw") or {}).get("climatological") is None:
            text += ["", f.lack("foresight.json") + " Nyckeln `median_kw.climatological` saknas i den."]
        kw = f.num("dayahead.median_kw", (fs.get("median_kw") or {}).get("climatological"),
                   src + ": median_kw.climatological", 0, "kW AC")
        kw_i = f.num("dayahead.median_kw_installation",
                     ((fs.get("installation") or {}).get("median_kw") or {}).get("climatological"),
                     src + ": installation.median_kw.climatological", 0, "kW AC")
        yr = f.text("dayahead.exceedance_year", e.get("year"), src + ": exceedance.year")
        agg = f.num("dayahead.pv_aggregate_hours_over", e.get("pv_aggregate_hours_over"), src + ": exceedance.pv_aggregate_hours_over")
        ratio = f.num("dayahead.pv_worst_aggregate_ratio_unmargined", e.get("pv_worst_aggregate_ratio_unmargined"),
                      src + ": exceedance.pv_worst_aggregate_ratio_unmargined", 4)
        under = f.num("dayahead.load_aggregate_hours_under", e.get("load_aggregate_hours_under"),
                      src + ": exceedance.load_aggregate_hours_under")
        cust = f.num("dayahead.load_customer_hours_under", e.get("load_customer_hours_under"),
                     src + ": exceedance.load_customer_hours_under")
        text += ["",
                 f"Perioden är {win} dagar. Med exportgränser utfärdade dagen före ryms {kw} kW med summerad budget och "
                 f"{kw_i} kW med budget per anläggning (median över ordningarna).",
                 "",
                 f"**Granskning av överskridanden.** Varje utfärdande räknar de timmar då året ändå slog planeringsläget. "
                 f"För {yr}: solelen i hela området låg över gränsen i {agg} timmar (utan marginal var det sämsta "
                 f"förhållandet {ratio}), och lasten i hela området under golvet i {under} timmar. För enskilda kunder "
                 f"låg lasten under golvet i {cust} kundtimmar; gränsen är empirisk, aldrig en garanti."]
    idx = _read(res, "operation/index.json")
    if idx is None:
        return "\n".join(text + ["", f.lack("operation/index.json")])
    from . import dso

    rule = dso.load().allocation_policy
    c = (idx.get("combos") or {}).get("dayahead", {}).get(rule) or {}
    src = f"results/operation/index.json: combos.dayahead.{rule}"
    lim = f.num("dayahead.hours_limited", c.get("hours_limited"), src + ".hours_limited")
    need = f.num("dayahead.necessary_hours", idx.get("necessary_hours"), "results/operation/index.json: necessary_hours")
    unneeded = f.num("dayahead.unnecessary_hours", c.get("activations_unnecessary_hours"),
                     src + ".activations_unnecessary_hours")
    return "\n".join(text + [
        "",
        f"Med nätbolagets regel begränsas {lim} timmar på grunden dagen före. Utan begränsning hade nätet "
        f"överbelastats i {need} timmar; {unneeded} timmar begränsades alltså utan att det behövdes. Ei:s villkor: "
        f"{_cite(ei, 'only_when_overloaded')}.",
    ])


def _intraday(f: Facts, res: Path) -> str:
    gates = _assumption(f, "intraday_gates_h", 0, sep=", ")
    lag = _assumption(f, "intraday_measurement_lag_h", 0)
    text = [f"Repliken planerar om exportgränserna under dygnet vid tidpunkterna kl. {gates} med de senaste "
            f"mätningarna, som når den med {lag} timmes fördröjning. Den är en efterhandsberäkning: den spelar upp "
            "väderåren och utfärdar ingenting."]
    d = _read(res, "intraday.json")
    if d is None:
        return "\n".join(text + ["", f.lack("intraday.json")])
    src = "results/intraday.json"
    sc = d.get("scorecard") or {}
    v = sc.get("violation_hours") or {}
    a = sc.get("agreements_within_pct") or {}
    g = sc.get("gain_over_hourly_pct") or {}
    kw = f.num("intraday.median_kw", d.get("median_kw"), src + ": median_kw", 0, "kW AC")
    hourly = f.num("intraday.hourly_kw", g.get("hourly_kw"), src + ": scorecard.gain_over_hourly_pct.hourly_kw", 0, "kW AC")

    def res_sv(key: str, block: dict) -> str:
        return f.text(f"intraday.scorecard.{key}.result", RESULT_SV.get(block.get("result"), block.get("result")),
                      f"{src}: scorecard.{key}.result")

    return "\n".join(text + [
        "",
        f"Med repliken ryms {kw} kW (median), mot {hourly} kW i den timvisa studien.",
        "",
        "| Krav | Utfall | Gräns | Bedömning |",
        "|---|---|---|---|",
        f"| Timmar med överträdelse | {f.num('intraday.violation_hours', v.get('value'), src + ': scorecard.violation_hours.value')} | "
        f"{f.num('intraday.violation_hours_threshold', v.get('threshold'), src + ': scorecard.violation_hours.threshold')} | "
        f"{res_sv('violation_hours', v)} |",
        f"| Avtal inom budget, sämsta ordningen | {f.num('intraday.agreements_within_pct', a.get('value'), src + ': scorecard.agreements_within_pct.value', 1, '%')} % | "
        f"{f.num('intraday.agreements_within_threshold', a.get('threshold'), src + ': scorecard.agreements_within_pct.threshold', 1, '%')} % "
        f"(med motivering {f.num('intraday.agreements_within_justified', a.get('justified_threshold'), src + ': scorecard.agreements_within_pct.justified_threshold', 1, '%')} %) | "
        f"{res_sv('agreements_within_pct', a)} |",
        f"| Vinst mot den timvisa studien | {f.num('intraday.gain_pct', g.get('value'), src + ': scorecard.gain_over_hourly_pct.value', 1, '%')} % | "
        f"{f.num('intraday.gain_threshold', g.get('threshold'), src + ': scorecard.gain_over_hourly_pct.threshold', 1, '%')} % | "
        f"{res_sv('gain_over_hourly_pct', g)} |",
        "",
        f"Sammantaget: **{f.text('intraday.scorecard.result', RESULT_SV.get(sc.get('result'), sc.get('result')), src + ': scorecard.result')}**.",
    ])


def _market(f: Facts, ei: dict) -> str:
    from . import dso, market

    s = dso.load()
    at = f.text("dso.market_result_local", s.market_result_local, "DSO settings: market_result_local "
                "(default config/assumptions.toml [dayahead_market_result_local])")
    records = market.entries()
    n = f.num("market.records", len(records), "data/requests/market_alternatives.jsonl (market.entries)")
    kinds = sorted({k for e in records for k in (e.get("kinds") or [])})
    stated = s.market_alternative_statement.strip()
    text = [
        f"Ett schema för dygnet D utfärdas först när resultatet från dagen före-marknaden för D är känt, kl. {at} "
        "(svensk lokal tid) dygnet före. Den som utfärdar tidigare måste ange ett skäl, som sigilleras med "
        f"utfärdandet. Ei:s villkor: {_cite(ei, 'after_market_result')}.",
        "",
        "Innan ett avtal tecknas och innan en period med aktiveringar registrerar nätbolaget vilka "
        "marknadsbaserade alternativ det har undersökt (" + ", ".join(KIND_SV.values()) + ") och med vilket "
        "utfall. Varje post läggs till i en logg och sigilleras; ett schema för ett dygn som ingen post täcker "
        f"flaggas men vägras aldrig. Ei:s villkor: {_cite(ei, 'market_first')}.",
        "",
        f"Registrerade poster: {n}"
        + (" (" + ", ".join(KIND_SV.get(k, k) for k in kinds) + ")." if kinds else "."),
        "Nätbolagets eget ställningstagande: " + ("angivet." if stated else "inte angivet; det kan bara nätbolaget göra."),
    ]
    return "\n".join(text)


def _redispatch(f: Facts, res: Path, ei: dict) -> str:
    text = ["Årsrapporten om icke-marknadsbaserad omdirigering räknas på grunden dagen före, den som utfärdas. "
            f"Ei:s villkor: {_cite(ei, 'annual_report')}."]
    r = _read(res, "redispatch.json")
    if r is None:
        return "\n".join(text + ["", f.lack("redispatch.json")])
    src = "results/redispatch.json"
    comp = (f"{f.num('redispatch.compensation_sek', r.get('compensation_sek'), src + ': compensation_sek', 0, 'SEK')} SEK"
            if r.get("compensation_entered") else "ingen angiven av nätbolaget")
    return "\n".join(text + [
        "",
        f"| | År {f.text('redispatch.year', r.get('year'), src + ': year')} |",
        "|---|---|",
        f"| Fördelningsregel | {f.text('redispatch.policy', POLICY_SV.get(r.get('policy'), r.get('policy')), src + ': policy')} |",
        f"| Nedreglerad energi | {f.num('redispatch.curtailed_mwh', r.get('curtailed_mwh'), src + ': curtailed_mwh', 1, 'MWh')} MWh |",
        f"| Timmar med nedreglering | {f.num('redispatch.hours', r.get('hours'), src + ': hours')}, varav "
        f"{f.num('redispatch.hours_needed', r.get('hours_needed'), src + ': hours_needed')} behövdes |",
        f"| Anläggningar som nedreglerades | {f.num('redispatch.installations', r.get('installations'), src + ': installations')} "
        f"av {f.num('redispatch.agreements', r.get('agreements'), src + ': agreements')} villkorade avtal |",
        f"| Andel av möjlig export | {f.num('redispatch.share_pct', r.get('share_of_potential_export_pct'), src + ': share_of_potential_export_pct', 1, '%')} % |",
        f"| Ersättning som betalats | {comp} |",
        f"| Timmar med överträdelse | {f.num('redispatch.verified_violations', r.get('verified_violations'), src + ': verified_violations')} |",
    ])


def _mv_demo(f: Facts, res: Path) -> str:
    base = _assumption(f, "mv_voltage_pu", 2, fixed=True)
    band = _assumption(f, "mv_voltage_band_pu", 2, fixed=True)
    text = [f"De publicerade exportgränserna räknas med mellanspänningen {base} p.u. vid nätstationen. Bandet "
            f"{band} p.u. är en separat publicerad känslighetsanalys, eftersom spänningen inte är uppmätt."]
    d = _read(res, "mv_band.json")
    if d is None:
        return "\n".join(text + ["", f.lack("mv_band.json")])
    src = "results/mv_band.json"
    text += ["", f"Samma anslutningsordning (nummer {f.num('mv.order', d.get('order'), src + ': order')}) räknas om "
                 "vid varje nivå. Den sista kolumnen visar vad de publicerade exportgränserna skulle ge där:", "",
             "| Mellanspänning | Statisk | Timvis | Med dynamiska gränser | Timmar med överträdelse, publicerade gränser |",
             "|---|---|---|---|---|"]
    for i, lv in enumerate(d.get("levels") or []):
        at = f"{src}: levels[{i}]"
        k = f"mv.{i}"
        text.append(f"| {f.num(k + '.vm_pu', lv.get('vm_pu'), at + '.vm_pu', 2, 'pu', True)} p.u. | "
                    f"{f.num(k + '.static_kw', lv.get('static_kw'), at + '.static_kw', 0, 'kW')} kW | "
                    f"{f.num(k + '.timeseries_kw', lv.get('timeseries_kw'), at + '.timeseries_kw', 0, 'kW')} kW | "
                    f"{f.num(k + '.doe_kw', lv.get('doe_kw'), at + '.doe_kw', 0, 'kW')} kW | "
                    f"{f.num(k + '.violation_hours', (lv.get('published') or {}).get('violation_hours'), at + '.published.violation_hours')} |")
    return "\n".join(text)


def _evidence(f: Facts, folder: Path | None) -> str:
    from contextlib import nullcontext

    from . import seals

    code = "kapacitet/seals.py"
    alg = f.text("seals.algorithm", seals.ALGORITHM, code + ": ALGORITHM")
    sha = f.text("seals.hash", "SHA-256", code + ": digest (hashlib.sha256)")
    info = seals.public_key_info(create=False)
    with seals.chain_at(folder) if folder is not None else nullcontext():
        out = seals.verify_chain()
    where = "i studiens mapp evidence/" if folder is not None else "i nätbolagets mapp data/requests eller i databasen"
    key = (f"Nyckelns id är `{f.text('seals.key_id', info['key_id'], code + ': public_key_info().key_id')}`."
           if info.get("key_id") else "Ingen signeringsnyckel har skapats ännu i den här installationen.")
    n = f.num("seals.length", out.get("length"), code + ": verify_chain().length")
    signed = f.num("seals.signed", out.get("signed", 0), code + ": verify_chain().signed")
    return "\n".join([
        f"Varje beslut, utfärdande och inställning sigilleras i en kedja, {where}. Varje rad har {sha}-summan av "
        f"det sigillerade innehållet och av raden före, och signeras med {alg}. Den publika nyckeln läggs som "
        f"`{seals.PUBLISHED}` bredvid varje kedja den har signerat i. {key}",
        "",
        f"Kedjan kontrollerades när underlaget skapades: {n} rader, varav {signed} signerade; "
        + ("den håller." if out.get("ok") else "**den håller inte**.")
        + f" Vem som helst kan kontrollera den med `kapacitet seals verify <mapp> --key {seals.PUBLISHED}`.",
    ])


def _flags(proc: Path) -> dict:
    """What this data folder holds of an operator's own data (as api/main.data_origin reads it)."""
    imported = (_read(proc, "grid/summary.json") or {}).get("imported") is True
    hh = proc / "households"
    metered = hh.is_dir() and any(hh.glob("metered_*.npy"))
    inventory = False
    cust = proc / "grid" / "customers.parquet"
    if cust.is_file():
        try:
            import pyarrow.parquet as pq

            inventory = any(n.startswith("existing_") for n in pq.read_schema(cust).names)
        except Exception:  # noqa: BLE001 - an unreadable table has no inventory
            inventory = False
    return {"imported": imported, "metered": metered, "inventory": inventory}


def _origin(f: Facts, flags: dict) -> tuple[list[str], list[str]]:
    real, synth = [], []
    if flags["imported"]:
        real.append("lågspänningsnätets topologi, kablar, kabelskåp, transformator och säkringar (nätbolagets export)")
    else:
        real.append("byggnader, adresser, gator och nätstationens läge (OpenStreetMap)")
        synth.append("lågspänningsnätets topologi, kablar, kabelskåp, transformator och säkringar (byggda längs "
                     "gatorna i OpenStreetMap)")
    real.append("väder (SMHI) och spotpriser")
    (real if flags["metered"] else synth).append(
        "hushållens last (uppmätt, temperaturkorrigerad)" if flags["metered"] else
        "hushållens last (CREST-modellen, skalad till svensk förbrukning)")
    (real if flags["inventory"] else synth).append(
        "befintliga solceller, batterier, elbilsladdare och värmepumpar (nätbolagets register)" if flags["inventory"]
        else "vilka hus som har solceller, värmepump eller elbil i dag (andelar, fasta slumpfrön)")
    synth.append("värmepumparnas och elbilarnas förbrukning och storleken på nya solcellsanläggningar (modellerade)")
    f.text("data.real", real, "data folder (grid/summary.json, households/, grid/customers.parquet)")
    f.text("data.synthetic", synth, "data folder (grid/summary.json, households/, grid/customers.parquet)")
    return real, synth


def _sources(f: Facts, raw: Path, fetch: str = "python -m kapacitet fetch") -> str:
    try:
        manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"Nedladdningsmanifestet (`raw/manifest.json`) saknas. Det skrivs av `{fetch}`."
    groups: dict[tuple[str, str], dict] = {}
    for e in manifest.values():
        if not isinstance(e, dict):
            continue
        k = (e.get("provider") or e.get("source") or "?", e.get("dataset") or "?")
        g = groups.setdefault(k, {"license": e.get("license") or "?", "attribution": e.get("attribution") or "",
                                  "files": 0, "years": set()})
        g["files"] += 1
        y = (e.get("params") or {}).get("year")
        if isinstance(y, int):
            g["years"].add(y)
    lines = ["| Källa | Datamängd | Licens | Filer | År |", "|---|---|---|---|---|"]
    for i, ((prov, ds), g) in enumerate(sorted(groups.items())):
        at = f"raw/manifest.json: provider {prov!r}, dataset {ds!r}"
        lines.append(f"| {f.text(f'source.{i}.provider', prov, at)} | {f.text(f'source.{i}.dataset', ds, at)} | "
                     f"{f.text(f'source.{i}.license', g['license'], at)} | {f.num(f'source.{i}.files', g['files'], at)} | "
                     f"{f.years(f'source.{i}.years', sorted(g['years']), at) if g['years'] else '–'} |")
    attr = sorted({g["attribution"] for g in groups.values() if g["attribution"]})
    if attr:
        lines += ["", "Krediteringar: " + "; ".join(f.text(f"attribution.{i}", a, "raw/manifest.json: attribution")
                                                     for i, a in enumerate(attr)) + "."]
    return "\n".join(lines)


def _limitations(f: Facts, kind: str, overrides: dict | None = None, flags: dict | None = None) -> str:
    marg = _assumption(f, "v_margin_pu", 3, overrides)
    items = [
        f"Modellen räknar med timvärden, medan spänningsgränsen gäller tiominutersmedelvärden; skillnaden täcks av "
        f"marginalen på {marg} p.u.",
        "Alla publicerade siffror kommer från den symmetriska trefasmodellen. Obalans mellan faserna ingår inte i "
        "dem; en ansökan med enfasig utrustning kontrolleras fas för fas med den osymmetriska modellen.",
    ]
    if kind == "demo":
        flags = flags or {}
        items.append("Fördelningen av exportgränserna bygger på perfekt framförhållning i planeringssiffran; det som "
                     "kan utfärdas är grunden dagen före, och skillnaden redovisas ovan.")
        if not flags.get("imported"):
            items.append("Lågspänningsnätet är syntetiskt, byggt längs verkliga gator.")
        if not flags.get("metered"):
            items.append("Hushållens last kommer från en beteendemodell som är skalad till svensk förbrukning.")
        items += ["Mellanspänningen hålls fast vid nätstationen; bandet ovan är en känslighetsanalys.",
                  "Ekonomin använder bara spotpriset i elområdet."]
    else:
        items += ["Värdena gäller lågspänningsnätet bakom varje nätstation och de gränser längre upp i nätet som "
                  "nätbolaget har lämnat; en nivå utan uppgift gissas aldrig.",
                  "Ett värde ersätter inte nätbolagets prövning av en enskild ansökan."]
    items.append("Underlaget visar vad modellen räknar och vad nätbolaget har angett. Om villkoren är uppfyllda i "
                 "lagens mening avgör Ei och domstol.")
    return "\n".join(f"- {x}" for x in items)


# ---------------------------------------------------------------- the sections, for a study

def _station_results(study) -> list[dict]:
    out = []
    for sid in study.station_ids():
        path = study.station_dir(sid) / "processed" / "results" / "capacity.json"
        try:
            out.append({"sid": sid, **json.loads(path.read_text(encoding="utf-8"))})
        except (OSError, ValueError):
            continue
    return out


def _computed_study(f: Facts, study, caps: list[dict]) -> str:
    cfg = study.config
    total = f.num("study.stations", len(study.station_ids()), "study: stations/")
    done = f.num("study.stations_computed", len(caps), "study: stations/<sid>/processed/results/capacity.json")
    src = "capacity.json of every computed station"
    pct = f.num("study.percentile", cfg.method.percentile, "study.toml [method] percentile", 1)
    device = f.text("study.consumption_device", DEVICE_SV[cfg.method.consumption_device],
                    "study.toml [method] consumption_device")
    years = f.years("study.years", [y for c in caps for y in (c.get("years") or [])], src + ": years")
    orders = sorted({int(c["orders"]) for c in caps if c.get("orders") is not None})
    o = f.text("study.orders", orders, src + ": orders", " eller ".join(str(x) for x in orders) or "–")
    g_ok = f.num("study.generation_ok", sum(1 for c in caps if (c.get("generation") or {}).get("status") == "ok"),
                 src + ": generation.status == ok")
    c_ok = f.num("study.consumption_ok", sum(1 for c in caps if (c.get("consumption") or {}).get("status") == "ok"),
                 src + ": consumption.status == ok")
    return "\n".join([
        f"Studien har {total} nätstationer, varav {done} är beräknade. För varje station räknas hur mycket ny "
        f"produktion (solceller) och ny förbrukning ({device}) som kan anslutas utan att någon gräns överskrids "
        f"under någon timme av väderåren {years}.",
        "",
        f"Anslutningar läggs till ett hus i taget i {o} slumpade ordningar; stationens värde är percentil {pct} av "
        "ordningarna, avrundad nedåt till en verklig ordning, och kontrolleras sedan med fullständig "
        "lastflödesberäkning för varje timme. Produktion räknas vid båda ändarna av mellanspänningsbandet och det "
        "lägre värdet publiceras; förbrukning vid den lägre änden.",
        "",
        f"Stationer med ett värde för produktion: {g_ok}. För förbrukning: {c_ok}. En station som redan i dag "
        "överskrider en gräns får ingen kapacitet; den redovisas som ett fynd, aldrig som noll.",
    ])


def _allocation_study(f: Facts, study, caps: list[dict], ei: dict) -> str:
    import inspect

    from .engine import doe

    rule = inspect.signature(doe.compute).parameters["policy"].default
    lines = [f"- **{POLICY_SV[p]}** (`{p}`): {POLICY_TEXT_SV[p]}." for p in POLICY_TEXT_SV]
    ref = study.config.method.approval_reference.strip()
    src = "capacity.json of every computed station: generation.doe.status"
    ok = f.num("study.doe_ok", sum(1 for c in caps if ((c.get("generation") or {}).get("doe") or {}).get("status") == "ok"), src)
    bad = f.num("study.doe_not_verified",
                sum(1 for c in caps if ((c.get("generation") or {}).get("doe") or {}).get("status") == "not_verified"), src)
    terms = f"Villkoren ska ange hur och när överföringen får begränsas: {_cite(ei, 'terms_how_when')}."
    if ref:
        lines += ["", f"Studiens villkorade kapacitet fördelas med regeln "
                      f"**{f.text('study.allocation_policy', POLICY_SV[rule], 'kapacitet/engine/doe.py: compute(policy=...)')}**. "
                      f"{terms}", ""]
        lines.append(f"Stationer med verifierad villkorad kapacitet: {ok}; stationer vars exportgränser inte "
                     f"verifierades och därför saknar värde: {bad}.")
    else:
        lines += ["", "Villkorad kapacitet räknas inte: nätbolaget har inte angett något godkännande av metoden. " + terms]
    return "\n".join(lines)


def _verification_study(f: Facts, study, caps: list[dict]) -> str:
    text = _verification(f, None, study.config.assumptions)
    src = "capacity.json of every computed station: verification"
    hrs = f.num("study.hours_checked", sum(int((c.get("verification") or {}).get("hours_checked") or 0) for c in caps),
                src + ".hours_checked (summed)")
    viol = f.num("study.violation_hours", sum(int((c.get("verification") or {}).get("full_year_violations") or 0) for c in caps),
                 src + ".full_year_violations (summed)")
    return text + f"\n\nI studiens beräknade stationer kontrollerades {hrs} timmar, med sammanlagt {viol} timmar med överträdelse."


def _budget_study(f: Facts, study) -> str:
    cfg = study.config
    z = _assumption(f, "z_star_pct", 1, cfg.assumptions)
    basis = f.text("study.budget", cfg.method.budget, "study.toml [method] budget",
                   "på summan för alla villkorade anläggningar" if cfg.method.budget == "aggregate"
                   else "för varje anläggning för sig, över alla väderår")
    return (f"Budgeten är högst {z} % av en anläggnings årliga solel, räknad {basis}. Med budget per anläggning "
            "styr andelen över alla väderår; det sämsta enskilda året redovisas men styr inte.")


def _mv_study(f: Facts, study, caps: list[dict]) -> str:
    cfg = study.config
    low = f.num("study.mv_low_pu", cfg.mv.band_low_pu, "study.toml [mv] band_low_pu", 2, "pu", True)
    high = f.num("study.mv_high_pu", cfg.mv.band_high_pu, "study.toml [mv] band_high_pu", 2, "pu", True)
    msrc = f.text("study.mv_source", cfg.mv.source, "study.toml [mv] source")
    src = "capacity.json of every computed station: generation.binding_mv_pu"
    at_low = f.num("study.binding_low", sum(1 for c in caps if (c.get("generation") or {}).get("binding_mv_pu") == cfg.mv.band_low_pu), src)
    at_high = f.num("study.binding_high", sum(1 for c in caps if (c.get("generation") or {}).get("binding_mv_pu") == cfg.mv.band_high_pu), src)
    return (f"Mellanspänningen vid stationen kan ligga var som helst mellan {low} och {high} p.u. ({msrc}). "
            "Kapaciteten är inte monoton i mellanspänningen, så produktion räknas vid båda ändarna och det lägre "
            f"värdet gäller; förbrukning räknas vid den lägre änden. Den lägre änden band produktionen i {at_low} "
            f"stationer och den högre i {at_high}.")


def _sources_study(f: Facts, study) -> str:
    from .study.station import weather_source

    def given(name: str) -> bool:
        p = study.inputs / name
        return p.is_file() or (p.is_dir() and any(p.iterdir()))

    real = ["lågspänningsnätet bakom varje nätstation (nätbolagets nätdata)"]
    synth = []
    if given("meters"):
        real.append("hushållens last (uppmätta timvärden där det finns tillräckligt många, annars syntetiska profiler)")
    else:
        synth.append("hushållens last (CREST-modellen; nätbolaget har inte lämnat mätvärden)")
    if given("equipment.csv"):
        real.append("befintlig utrustning (nätbolagets register)")
    else:
        synth.append("befintlig utrustning: inget register, så ingen utrustning räknas som befintlig utöver nätet")
    if given("upstream.csv"):
        real.append("ledig kapacitet längre upp i nätet (nätbolagets uppgifter)")
    if given("mv"):
        real.append("mellanspänningen vid stationen (uppmätt)")
    else:
        synth.append("mellanspänningen vid stationen (planeringsband, inte uppmätt)")
    if weather_source(study)["source"] == "study":
        real.append("väder (SMHI, för studiens plats)")
    else:
        synth.append("väder: referensnätets väderår, eftersom studien inte har eget väder för sin plats")
    synth.append("värmepumparnas och elbilarnas förbrukning och storleken på nya solcellsanläggningar (modellerade)")
    src = "study: inputs/ and the weather source (study/station.py: weather_source)"
    f.text("data.real", real, src)
    f.text("data.synthetic", synth, src)
    return "\n".join(["Verkliga uppgifter:", *[f"- {x}" for x in real], "", "Syntetiska uppgifter:",
                      *[f"- {x}" for x in synth], "", _sources(f, study.raw, "python -m kapacitet study fetch")])


# ---------------------------------------------------------------- assembly

def _header(f: Facts, subject: str, approval: str, ei: dict, computed_at: str | None) -> dict[str, str]:
    from .study import code_version
    from .study.config import METHOD_VERSION

    code = code_version()
    today = dt.date.today().isoformat()
    approved = (f"Nätbolaget har angett godkännandet {f.text('approval_reference', approval, 'approval reference')}."
                if approval else "Nätbolaget har inte angett något godkännande av metoden.")
    return {
        "subject": f.text("subject", subject, "site or study"),
        "generated_at": f.text("generated_at", today, "the date the dossier was written"),
        "code_version": f.text("code.version", str(code.get("version")), "kapacitet.__version__"),
        "code_commit": f.text("code.commit", (code.get("commit") or "okänd")[:12], "git rev-parse HEAD"),
        "method_version": f.text("method_version", METHOD_VERSION, "kapacitet/study/config.py: METHOD_VERSION"),
        "computed_at": f.text("computed_at", computed_at[:10], "results") if computed_at else "–",
        "ei_document": f.items["ei.document"]["text"],
        "ei_method": _cite(ei, "method_approved"),
        "approval": approved,
    }


def build(study_dir: str | Path | None = None, *, processed_dir: Path | None = None,
          raw_dir: Path | None = None) -> tuple[str, dict]:
    """The dossier's Swedish text and its JSON (every fact with its source), for the demo or a study."""
    from . import paths

    f = Facts()
    ei = _ei(f)
    if study_dir is None:
        proc = processed_dir or paths.processed_ro()
        res = proc / "results"
        meta = _read(res, "meta.json") or {}
        flags = _flags(proc)
        site = meta.get("site") or (_read(proc, "grid/summary.json") or {}).get("site") or "–"
        from . import dso

        values = _header(f, site, dso.load().method_approval_reference.strip(), ei, meta.get("computed_at"))
        real, synth = _origin(f, flags)
        values.update({
            "kind_note": ("Siffrorna kommer från ett syntetiskt nät byggt på verkliga gator och från syntetisk last; "
                          "de är simulerade, inte uppmätta." if not flags["imported"] else
                          "Nätet är nätbolagets eget (importerat); vad som är verkligt och syntetiskt står under Data."),
            "computed": _computed_demo(f, res),
            "allocation": _allocation_demo(f, res, ei),
            "verification": _verification(f, res),
            "budget": _budget_demo(f, res),
            "dayahead": _dayahead(f, res, ei),
            "intraday": _intraday(f, res),
            "market": _market(f, ei),
            "redispatch": _redispatch(f, res, ei),
            "mv": _mv_demo(f, res),
            "evidence": _evidence(f, None),
            "data": "\n".join(["Verkliga uppgifter:", *[f"- {x}" for x in real], "", "Syntetiska uppgifter:",
                               *[f"- {x}" for x in synth], "", _sources(f, raw_dir or paths.RAW)]),
            "limitations": _limitations(f, "demo", flags=flags),
        })
        kind, where = "demo", str(proc)
    else:
        from .study import open_study

        study = open_study(study_dir, allow_git=True)
        caps = _station_results(study)
        stamps = sorted(c.get("computed_at") or "" for c in caps)
        values = _header(f, f"{study.config.dso.name or study.id}, studie {study.id}",
                         study.config.method.approval_reference.strip(), ei, stamps[-1] if stamps and stamps[-1] else None)
        off = "Ingår inte i en studie: den beskriver den dagliga driften av villkorade avtal, som nätbolaget inför efter godkännandet."
        values.update({
            "kind_note": "Siffrorna kommer från nätbolagets nätdata i studien; vad som är verkligt och syntetiskt står under Data.",
            "computed": _computed_study(f, study, caps),
            "allocation": _allocation_study(f, study, caps, ei),
            "verification": _verification_study(f, study, caps),
            "budget": _budget_study(f, study),
            "dayahead": off, "intraday": off, "market": off, "redispatch": off,
            "mv": _mv_study(f, study, caps),
            "evidence": _evidence(f, study.evidence),
            "data": _sources_study(f, study),
            "limitations": _limitations(f, "study", study.config.assumptions),
        })
        kind, where = "study", str(study.root)
    text = render(TEMPLATE.read_text(encoding="utf-8"), values)
    doc = {"schema": SCHEMA, "kind": kind, "source": where, "facts": f.items, "missing": f.missing}
    return text, doc


def render(template: str, values: dict[str, str]) -> str:
    """The template with every {{placeholder}} filled; one without a value raises KeyError."""
    missing = sorted({m for m in PLACEHOLDER.findall(template) if m not in values})
    if missing:
        raise KeyError(f"no value for {', '.join(missing)}")
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


def numbers(text: str) -> list[str]:
    return NUMBER.findall(text)


def write(study_dir: str | Path | None = None, out: str | Path | None = None) -> tuple[Path, Path]:
    """Write method_dossier_sv.md and method_dossier.json: into the study's folder, or the demo's results."""
    from . import paths
    from .atomic import write_json_atomic, write_text_atomic

    text, doc = build(study_dir)
    if out is not None:
        folder = Path(out)
    elif study_dir is not None:
        from .study import open_study

        folder = open_study(study_dir, allow_git=True).root
    else:
        folder = paths.processed("results", TEXT_NAME).parent
    folder.mkdir(parents=True, exist_ok=True)
    write_text_atomic(folder / TEXT_NAME, text)
    write_json_atomic(folder / JSON_NAME, doc)
    return folder / TEXT_NAME, folder / JSON_NAME


__all__ = ["JSON_NAME", "SCHEMA", "TEMPLATE", "TEXT_NAME", "Facts", "build", "numbers", "render", "sv_number", "write"]
