"""The embeddable capacity map, the publication API and monthly runs (study/embed.py,
api/publications.py, study/runs.py), on the demo study with stand-in station results: the demo's
networks are pandapower's, and a build needs the reference network's data, so each station's
capacity.json is written here as a build would write it."""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest

from kapacitet.study import StudyError, open_study, runs
from kapacitet.study import collect as collect_mod
from kapacitet.study.config import METHOD_VERSION, StudyConfigError, parse
from kapacitet.study.diff import diff
from kapacitet.study.publish import DRAFT, SIGNED, publish, sign
from kapacitet.study.review import review

Q3, Q4, NOV = "2026Q3", "2026Q4", "2026-11"
SIGNER = "Anna Exempel, nätchef"
# the demo's three stations, with the figures a build would have written
FIGURES = {"demo-dickert": (40, 0.000, 0.000, 120.0, 80.0), "demo-landnetz": (30, 0.012, 0.004, 64.5, None),
           "demo-dorfnetz": (12, -0.006, 0.010, 7.5, 31.0)}


def capacity(sid: str, gen: float | None, cons: float | None) -> dict:
    g = ({"status": "ok", "kw": gen, "binding_mv_pu": 1.06, "binding": {"limit": "voltage"}, "order": 3,
          "doe": {"status": "not_approved"}} if gen is not None else
         {"status": "baseline_violates", "kw": None, "baseline_violation_hours": {"2025": 4}})
    c = ({"status": "ok", "kw": cons, "consumption_device": "ev", "binding": {"limit": "transformer"}}
         if cons is not None else {"status": "baseline_violates", "kw": None, "consumption_device": "ev",
                                   "baseline_violation_hours": {"2025": 7}})
    return {"schema": "kapacitet.capacity/1", "station": sid, "computed_at": "2026-11-01T00:00:00+00:00",
            "method_version": METHOD_VERSION, "fingerprint": "f", "mv_band_pu": [0.98, 1.06], "years": [2024, 2025],
            "orders": 5, "percentile": 50.0, "generation": g, "consumption": c, "worst_point": {},
            "verification": {"full_year_violations": 0, "nonconverged_hours": 0, "hours_checked": 1}}


def computed(study, sid: str, gen=None, cons=None) -> None:
    """A station as its build leaves it: ready, with its busbar's position and its capacity.json."""
    n, dlon, dlat, g, c = FIGURES[sid]
    gen = g if gen is None else gen
    folder = study.station_dir(sid)
    (folder / "processed" / "results").mkdir(parents=True, exist_ok=True)
    (folder / "processed" / "grid").mkdir(parents=True, exist_ok=True)
    rec = study.record(sid)
    rec.update(status="ready", summary={"customers": n})
    study.save(rec)
    site = study.config.site
    pos = {"lon": site.lon + dlon, "lat": site.lat + dlat}
    (folder / "processed" / "grid" / "substation.json").write_text(json.dumps(pos), encoding="utf-8")
    (folder / "processed" / "results" / "capacity.json").write_text(json.dumps(capacity(sid, gen, c if cons is None
                                                                                          else cons)), encoding="utf-8")


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    from kapacitet.study.demo import create

    s = create(tmp_path_factory.mktemp("embed-template") / "demo")
    for sid in FIGURES:
        computed(s, sid)
    return s.root


@pytest.fixture()
def study(tmp_path, _template):
    shutil.copytree(_template, tmp_path / "studies" / "demo")
    return open_study(tmp_path / "studies" / "demo")


def signed_publication(study, run: str = Q4):
    collect_mod.collect(study, run)
    review(study, run)
    sign(study, run, SIGNER)
    out = publish(study, run)
    assert out["status"] == SIGNED
    return out


# ---------------------------------------------------------------- the embed

class Refs(HTMLParser):
    """Every URL an HTML page asks for, every inline script or style, every event-handler attribute."""

    def __init__(self):
        super().__init__()
        self.urls, self.inline, self.handlers, self.ids, self._in = [], [], [], {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href", "action", "poster", "data"):
            if a.get(key):
                self.urls.append(a[key])
        if tag == "script" and not a.get("src"):
            self._in = "script"
        if tag == "style" or "style" in a:
            self.inline.append(tag)
        self.handlers += [k for k in a if k.startswith("on")]
        if a.get("id"):
            self.ids[a["id"]] = a

    def handle_data(self, data):
        if self._in == "script" and data.strip():
            self.inline.append("script")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = None


def page(folder: Path) -> Refs:
    refs = Refs()
    refs.feed((folder / "embed" / "index.html").read_text(encoding="utf-8"))
    return refs


EXTERNAL = re.compile(r"""(?:https?:)?//[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}|url\(\s*['"]?(?:https?:)?//|@import""")


def test_the_embed_is_written_next_to_the_publication_and_asks_for_nothing_outside_it(study):
    collect_mod.collect(study, Q4)
    out = publish(study, Q4)
    folder = out["folder"]
    assert {p.name for p in (folder / "embed").iterdir()} == {"index.html", "embed.js", "embed.css"}
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name in ("embed/index.html", "embed/embed.js", "embed/embed.css"):
        assert name in manifest["files"], name
    assert manifest["embed"] == {"index": "embed/index.html", "map_tiles": None, "map_attribution": None}
    assert manifest["period"] == {"kind": "quarter", "first_day": "2026-10-01", "last_day": "2026-12-31"}

    refs = page(folder)
    assert not refs.inline and not refs.handlers          # runs under default-src 'self'
    base = "https://dso.example/kapacitet/runs/2026Q4/publication/embed/index.html"
    inside = "https://dso.example/kapacitet/runs/2026Q4/publication/"
    for url in refs.urls:
        absolute = urljoin(base, url)
        assert absolute.startswith(inside), url
        assert (folder / absolute[len(inside):]).is_file(), url
    js = (folder / "embed" / "embed.js").read_text(encoding="utf-8")
    css = (folder / "embed" / "embed.css").read_text(encoding="utf-8")
    # the only addresses in the script are the namespace SVG elements are made in, and the two files
    found = {m.group(0) for m in EXTERNAL.finditer(js)} - {"http://www.w3.org"}
    assert not found, found
    assert not EXTERNAL.search(css)
    for name in re.findall(r"""getJSON\("([^"]+)"\)""", js):
        assert (folder / "embed" / name).resolve().is_file(), name
    assert set(re.findall(r"""getJSON\("([^"]+)"\)""", js)) == {"../manifest.json", "../capacity.geojson"}


def test_the_page_has_every_cell_in_its_table_and_says_utkast_while_a_draft(study):
    collect_mod.collect(study, Q4)
    folder = publish(study, Q4)["folder"]
    text = (folder / "embed" / "index.html").read_text(encoding="utf-8")
    refs = page(folder)
    # a draft: the mark across the map and the note are shown, and the meta line says so
    assert "hidden" not in refs.ids["embed-draft-mark"] and "hidden" not in refs.ids["embed-draft-note"]
    assert 'class="is-draft"' in text and "UTKAST" in text and "Kvartal 2026Q4" in text
    assert f"metodversion {METHOD_VERSION}" in text
    # the table: one row per published cell, a figure without a value says so
    cells = json.loads((folder / "capacity.geojson").read_text(encoding="utf-8"))["features"]
    assert text.count('<th scope="row">') == len(cells) > 0
    assert "120" in text and "7,5" in text and "–" in text
    assert "<noscript>" in text and "Visa som tabell" in text
    # an attribution only with tiles
    assert "hidden" in refs.ids["embed-attribution"]


def test_a_signed_page_names_the_signers_role_and_never_the_name(study):
    folder = signed_publication(study)["folder"]
    text = (folder / "embed" / "index.html").read_text(encoding="utf-8")
    refs = page(folder)
    assert "hidden" in refs.ids["embed-draft-mark"] and 'class="is-signed"' in text
    assert "signerad av nätchef" in text and "Anna Exempel" not in text


def test_tiles_are_named_in_the_manifest_with_their_attribution(study):
    text = study.toml.read_text(encoding="utf-8")
    text = text.replace("# map_tiles = \"https://tiles.example/{z}/{x}/{y}.png\"",
                        'map_tiles = "https://tiles.example/{z}/{x}/{y}.png"')
    text = text.replace("# map_attribution = \"© the tile provider\"", 'map_attribution = "© Tile Provider"')
    study.toml.write_text(text, encoding="utf-8")
    study = open_study(study.root)
    collect_mod.collect(study, Q4)
    folder = publish(study, Q4)["folder"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["embed"]["map_tiles"] == "https://tiles.example/{z}/{x}/{y}.png"
    assert manifest["embed"]["map_attribution"] == "© Tile Provider"
    refs = page(folder)
    assert "hidden" not in refs.ids["embed-attribution"]
    assert "© Tile Provider" in (folder / "embed" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("section, why", [
    ('map_tiles = "https://tiles.example/{z}/{x}/{y}.png"', "map_attribution"),
    ('map_tiles = "http://tiles.example/{z}/{x}/{y}.png"\nmap_attribution = "x"', "https"),
    ('map_tiles = "https://tiles.example/tile.png"\nmap_attribution = "x"', "{z}"),
    ('map_attribution = "x"', "without map_tiles"),
])
def test_study_check_refuses_tiles_without_attribution_or_https(study, section, why, capsys):
    from kapacitet.pipeline import main

    with pytest.raises(StudyConfigError, match=re.escape(why)):
        parse({"publication": tomllib.loads(section)})
    study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
        'cadence = "quarterly"', 'cadence = "quarterly"\n' + section), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        main(["study", "check", str(study.root)])
    assert stop.value.code != 0 and why in capsys.readouterr().out


# ---------------------------------------------------------------- monthly runs

def test_run_ids_are_quarters_or_months_ordered_in_time(tmp_path):
    assert runs.kind(Q4) == "quarter" and runs.kind(NOV) == "month"
    assert not any(runs.valid(r) for r in ("2026Q5", "2026-13", "2026-1", "26Q4", "latest", "", None))
    assert runs.period(NOV) == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert runs.period("2028-02")[1] == dt.date(2028, 2, 29)
    assert sorted(["2026-11", "2026Q4", "2026-10", "2026Q3"], key=runs.key) == ["2026Q3", "2026-10", "2026Q4", "2026-11"]
    assert runs.current("monthly", dt.date(2026, 11, 7)) == NOV and runs.current("quarterly", dt.date(2026, 11, 7)) == Q4
    assert runs.word_sv(NOV) == "månad" and runs.label_sv(Q4) == "Kvartal"
    for r in (Q3, NOV):
        (tmp_path / r).mkdir()
        (tmp_path / r / "stations.parquet").write_bytes(b"")
    assert runs.previous(tmp_path, NOV) == Q3 and runs.previous(tmp_path, Q3) is None


def test_a_monthly_run_collects_signs_publishes_and_diffs_against_the_previous_quarter(study, capsys):
    from kapacitet.pipeline import main

    collect_mod.collect(study, Q3)
    computed(study, "demo-dickert", gen=110.0)          # computed again since the quarter
    root = str(study.root)
    study.toml.write_text(study.toml.read_text(encoding="utf-8").replace('cadence = "quarterly"', 'cadence = "monthly"'),
                          encoding="utf-8")
    study = open_study(study.root)
    assert collect_mod.collect(study)["quarter"] == runs.current("monthly")     # the cadence's default
    main(["study", "collect", root, "--run", NOV])
    main(["study", "review", root, "--run", NOV])
    main(["study", "sign", root, "--run", NOV, "--by", SIGNER])
    main(["study", "publish", root, "--run", NOV])
    main(["study", "diff", root, "--to", NOV])
    text = capsys.readouterr().out
    assert f"{NOV}.1" in text and SIGNED in text and f"since {Q3}" in text
    folder = study.runs / NOV / "publication"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == SIGNED and manifest["quarter"] == NOV and manifest["period"]["kind"] == "month"
    assert "| Månad | 2026-11 |" in (folder / "grunder_for_berakning.md").read_text(encoding="utf-8")
    assert f"månad {NOV}" in (folder / "validation_report.sv.md").read_text(encoding="utf-8")
    assert "Månad 2026-11" in (folder / "embed" / "index.html").read_text(encoding="utf-8")
    out = diff(study, None, NOV)
    assert out["from"] == Q3
    by = {(r["station_id"], r["field"]): r for r in out["rows"]}
    assert by[("demo-dickert", "published_generation_kw")]["delta_kw"] == pytest.approx(-10.0)
    with pytest.raises(StudyError):
        diff(study, None, Q3)                          # nothing before the first run
    with pytest.raises(StudyError):
        collect_mod.collect(study, "2026-13")


# ---------------------------------------------------------------- the API

@pytest.fixture()
def api(monkeypatch, study):
    from fastapi.testclient import TestClient

    from kapacitet.api import guard
    from kapacitet.api.main import app

    guard.LIMITER.reset()
    monkeypatch.setenv("KAPACITET_PUBLICATIONS", "1")
    monkeypatch.setenv("KAPACITET_STUDY_DIR", str(study.root.parent))
    with TestClient(app) as c:
        yield c
    guard.LIMITER.reset()


def code(r) -> str:
    """The refusal's code; "no_route" when the client's own normalisation of `..` left no route."""
    detail = r.json()["detail"]
    return detail["code"] if isinstance(detail, dict) else "no_route"


def test_the_api_is_off_without_the_variable(api, monkeypatch):
    monkeypatch.delenv("KAPACITET_PUBLICATIONS")
    for path in ("/api/v1/publications", "/api/v1/publications/demo/latest/manifest.json"):
        r = api.get(path)
        assert r.status_code == 404 and code(r) == "publications_off"
        assert "KAPACITET_PUBLICATIONS=1" in r.json()["detail"]["message"]


def test_the_api_refuses_a_draft_and_lists_only_signed_publications(api, study):
    collect_mod.collect(study, Q4)
    publish(study, Q4)                                  # a draft
    assert api.get("/api/v1/publications").json() == {"publications": []}
    for run in (Q4, "latest"):
        r = api.get(f"/api/v1/publications/demo/{run}/embed/index.html")
        assert r.status_code == 404 and code(r) == "no_publication"


def test_the_api_serves_the_latest_signed_publication_with_its_sha256_as_etag(api, study):
    signed_publication(study, Q3)
    signed_publication(study, NOV)
    collect_mod.collect(study, "2026-12")
    publish(study, "2026-12")                           # a later draft is never the latest
    listed = api.get("/api/v1/publications").json()["publications"]
    assert [p["id"] for p in listed] == ["demo"]
    entry = listed[0]
    assert entry["latest_run"] == NOV and entry["runs"] == [NOV, Q3] and entry["method_version"] == METHOD_VERSION
    assert "embed/index.html" in entry["files"] and "manifest.json" in entry["files"]
    assert entry["embed"] == f"/api/v1/publications/demo/{NOV}/embed/index.html"
    manifest = json.loads((study.runs / NOV / "publication" / "manifest.json").read_text(encoding="utf-8"))

    r = api.get("/api/v1/publications/demo/latest/capacity.geojson")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/geo+json")
    assert r.headers["etag"] == f'"{manifest["files"]["capacity.geojson"]["sha256"]}"'
    assert r.json()["quarter"] == NOV
    again = api.get("/api/v1/publications/demo/latest/capacity.geojson", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    page_ = api.get(f"/api/v1/publications/demo/{NOV}/embed/index.html")
    assert page_.status_code == 200 and page_.headers["content-security-policy"].startswith("default-src 'self'")
    assert "UTKAST" in page_.text and "is-signed" in page_.text
    js = api.get(f"/api/v1/publications/demo/{NOV}/embed/embed.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    m = api.get(f"/api/v1/publications/demo/{Q3}/manifest.json")
    assert m.status_code == 200 and m.headers["etag"].startswith('"') and not m.headers["etag"].startswith("W/")


@pytest.mark.parametrize("path", ["disclosure_report.csv", "../disclosure_report.csv", "..%2Fdisclosure_report.csv",
                                  "../signoff.json", "embed/../../signoff.json", "nothing.csv", "embed/", "embed/x.js"])
def test_the_api_serves_only_the_publications_own_files(api, study, path):
    signed_publication(study)
    r = api.get(f"/api/v1/publications/demo/{Q4}/{path}")
    assert r.status_code == 404 and code(r) in ("publication_file", "no_publication", "no_route"), (path, r.status_code)
    assert "signoff" not in r.text and "station_id" not in r.text


def test_a_publication_changed_after_it_was_written_is_not_served(api, study):
    folder = signed_publication(study)["folder"]
    assert api.get(f"/api/v1/publications/demo/{Q4}/capacity.csv").status_code == 200
    (folder / "capacity.csv").write_text("status;cell_id\nsignerad;made-up\n", encoding="utf-8")
    r = api.get(f"/api/v1/publications/demo/{Q4}/capacity.csv")
    assert r.status_code == 404 and code(r) == "no_publication"
    assert api.get("/api/v1/publications").json() == {"publications": []}
    for bad in ("Q4", "../demo", "nope"):
        assert api.get(f"/api/v1/publications/{bad}/{Q4}/capacity.csv").status_code == 404


def test_instance_check_reports_the_publications(monkeypatch, study):
    from kapacitet import instance

    monkeypatch.delenv("KAPACITET_PUBLICATIONS", raising=False)
    found = []
    instance._check_publications(found)
    assert found[0].level == "ok" and found[0].message.startswith("off")
    monkeypatch.setenv("KAPACITET_PUBLICATIONS", "1")
    monkeypatch.delenv("KAPACITET_STUDY_DIR", raising=False)
    found = []
    instance._check_publications(found)
    assert found[0].level == "error"
    monkeypatch.setenv("KAPACITET_STUDY_DIR", str(study.root.parent))
    signed_publication(study)
    found = []
    instance._check_publications(found)
    assert found[0].level == "ok" and "1 of 1 studies" in found[0].message
