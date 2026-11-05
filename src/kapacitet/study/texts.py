"""The Swedish sentences a case's annex says, taken from the interface's own dictionary.

The reasons a static study gives (`req.reason.*`, engine/request._REASONS) and those the rule gives
(`inbox.reason.*`, engine/rules.REASONS) are translated once, in web/src/lib/i18n/sv.ts, with the
glossary's terms; the annex reads them from there rather than keeping a second translation that
could drift from the one on screen. The file is the TypeScript dictionary, one `"key": "text",`
per line, so it is read as text. Where the sources are not installed (a container built from
web/dist alone) the engine's English sentence stands in, and `missing()` says so.

The placeholders are the engine's: `{v}` in a static reason, `{value}`, `{limit}` and `{detail}` in
the rule's, filled with the numbers written the Swedish way (a decimal comma).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..cache import memo
from ..paths import ROOT

SV_TS = ROOT / "web" / "src" / "lib" / "i18n" / "sv.ts"
_LINE = re.compile(r'^\s*"(?P<key>[a-zA-Z0-9_.-]+)":\s*"(?P<text>(?:[^"\\]|\\.)*)",?\s*$')
_SLOT = re.compile(r"\{([a-z_]+)\}")
# the decimals a static reason's value is written with, as the engine's English sentence has it
_PU = ("over_v", "under_v")


@memo(maxsize=1)
def dictionary(path: Path | None = None) -> dict[str, str]:
    """Every entry of sv.ts, or {} when the file is not there."""
    p = Path(path) if path is not None else SV_TS
    if not p.is_file():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m:
            out[m["key"]] = json.loads(f'"{m["text"]}"')
    return out


def _number(x, decimals: int) -> str:
    from .publish import sv

    return sv(x, decimals)


def _fill(template: str, params: dict) -> str:
    def value(m: re.Match) -> str:
        v = params.get(m.group(1))
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return _number(v, params.get("_decimals", 1))
        return "–" if v is None else str(v)

    return _SLOT.sub(value, template)


def static_reason(code: str, value=None) -> str:
    """A static study's reason (req.reason.<code>) in Swedish, its value in place."""
    from ..engine.request import _REASONS

    text = dictionary().get(f"req.reason.{code}")
    if text is None:
        text = _REASONS[code].replace(":.0f}", "}").replace(":.3f}", "}")
    return _fill(text, {"v": value, "_decimals": 3 if code in _PU else 0})


def rule_reason(code: str, params: dict | None = None) -> str:
    """The rule's reason (inbox.reason.<code>) in Swedish."""
    from ..engine.rules import REASONS

    text = dictionary().get(f"inbox.reason.{code}")
    if text is None:
        text = REASONS[code].replace(":.1f}", "}").replace(":.0f}", "}")
    return _fill(text, {**(params or {}), "_decimals": 1})


def missing() -> bool:
    """Whether the Swedish dictionary is absent, so the English sentences stand in."""
    return not dictionary()


__all__ = ["SV_TS", "dictionary", "missing", "rule_reason", "static_reason"]
