"""Conventions of the web interface that a reviewer would otherwise check by eye.

Standard library only, so it runs on a fresh clone without Node or the pipeline output:
- every interface string exists in English and in Spanish, and every literal key a component
  asks for exists;
- every screen has its tab label;
- numbers are formatted through lib/format.ts (no `.toFixed(` for display);
- nothing scrolls with `scrollIntoView(` except the palette's own list (it would lift the whole
  frame off screen, see lib/scroll.ts);
- colours come from tokens: no raw hex outside styles/tokens.css and lib/colors.ts;
- money is SEK, never kr (DESIGN.md), and dates are formatted only in lib/format.ts.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web" / "src"
SOURCES = [p for p in WEB.rglob("*") if p.suffix in (".ts", ".tsx")]


def _dict_keys(name: str) -> set[str]:
    text = (WEB / "lib" / "i18n.ts").read_text(encoding="utf-8")
    start = text.index(f"const {name}: Dict = {{")
    end = text.index("\n};", start)
    return set(re.findall(r'^\s+"([^"]+)":', text[start:end], re.M))


def test_english_and_spanish_have_the_same_keys():
    en, es = _dict_keys("en"), _dict_keys("es")
    assert en, "no English keys found"
    assert not en - es, f"missing in Spanish: {sorted(en - es)[:20]}"
    assert not es - en, f"missing in English: {sorted(es - en)[:20]}"


def test_every_literal_key_asked_for_exists():
    keys = _dict_keys("en")
    used: dict[str, str] = {}
    pat = re.compile(r"""\bt\(\s*(["'`])([a-zA-Z0-9_.]+)\1""")
    for p in SOURCES:
        for m in pat.finditer(p.read_text(encoding="utf-8")):
            used.setdefault(m.group(2), p.name)
    missing = {k: f for k, f in used.items() if k not in keys}
    assert not missing, f"keys used but not defined: {missing}"


def test_every_screen_has_a_tab_label():
    store = (WEB / "lib" / "store.ts").read_text(encoding="utf-8")
    screens = re.findall(r'"(\w+)"', re.search(r"export const SCREENS: Screen\[\] = \[(.*?)\]", store).group(1))
    keys = _dict_keys("en")
    assert screens
    for s in screens:
        assert f"tab.{s}" in keys, f"screen {s} has no tab label"


def test_numbers_are_not_formatted_with_toFixed():
    hits = [f"{p.name}:{i + 1}" for p in SOURCES for i, line in enumerate(p.read_text(encoding="utf-8").splitlines())
            if ".toFixed(" in line]
    assert not hits, f"format numbers with lib/format.ts, not toFixed: {hits}"


def test_only_the_palette_scrolls_into_view():
    hits = [p.name for p in SOURCES if ".scrollIntoView(" in p.read_text(encoding="utf-8") and p.name != "Palette.tsx"]
    assert not hits, f"use lib/scroll.ts instead of scrollIntoView in {hits}"


def test_money_is_sek_never_kr():
    kr = re.compile(r"\bkr\b")
    hits = [f"{p.name}:{i + 1}" for p in SOURCES for i, line in enumerate(p.read_text(encoding="utf-8").splitlines())
            if kr.search(line)]
    assert not hits, f"write SEK, not kr: {hits}"


def test_dates_are_formatted_in_one_place():
    hits = [p.name for p in SOURCES if "Intl.DateTimeFormat" in p.read_text(encoding="utf-8") and p.name != "format.ts"]
    assert not hits, f"format dates with dateTime() or clock() from lib/format.ts, not in {hits}"


def test_colours_come_from_tokens():
    hexcol = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
    allowed = {"tokens.css", "colors.ts"}
    hits = []
    for p in [*SOURCES, *WEB.rglob("*.css")]:
        if p.name in allowed:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            if hexcol.search(line):
                hits.append(f"{p.name}:{i + 1}")
    assert not hits, f"raw hex colours outside the tokens: {hits}"
