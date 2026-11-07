"""The embeddable capacity map: runs/<run>/publication/embed/, written by `study publish`.

    embed/index.html    the page, rendered here: its header (the DSO, the period, the signer's role,
                        the method version), UTKAST across the map while the publication is a draft,
                        and the table of every published cell, which is all a reader without script
                        (or with a screen reader) needs
    embed/embed.js      draws the cells of ../capacity.geojson as SVG in a Web Mercator projection
                        fitted to their extent, reads ../manifest.json for the status and the period,
                        and adds the switch, the panel, zoom and pan; Swedish, English with ?lang=en
    embed/embed.css     its styles

No library, no CDN, no inline script or style: the folder runs under
`Content-Security-Policy: default-src 'self'` and makes no request outside itself, unless
`[publication] map_tiles` names a raster tile layer (with `map_attribution`), which embed.js draws
under the cells and the page credits. The page shows the signer's role, never a name: a signoff is
"name, role", and only the part after the comma is shown. A DSO hosts the folder next to the
publication's other files (the page reads them as ../) and embeds it with an iframe
(docs/publication.md).
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from ..atomic import write_text_atomic
from . import runs

SOURCE = Path(__file__).with_name("templates") / "embed"
STATIC = ("embed.js", "embed.css")
FILES = ("index.html", *STATIC)
LEVEL_SV = {"station": "nätstation", "grid": "ruta", "zone": "område", "study": "hela studien"}
# the table's columns: the published column and its Swedish header; the optional ones only when the
# publication has them
COLUMNS = (
    ("name", "Namn"), ("cell_id", "Cell"), ("level", "Nivå"), ("customers", "Kunder"),
    ("generation_kw", "Produktion (kW)"), ("consumption_kw", "Förbrukning (kW)"),
    ("conditional_generation_kw", "Villkorad produktion (kW)"),
    ("three_phase_generation_kw", "Produktion, trefas (kW)"),
    ("three_phase_consumption_kw", "Förbrukning, trefas (kW)"),
)
NUMERIC = {"customers", "generation_kw", "consumption_kw", "conditional_generation_kw",
           "three_phase_generation_kw", "three_phase_consumption_kw"}
NO_VALUE = "–"
THIN = " "     # the thin no-break space that groups thousands in Swedish


def number(x, unit: bool = False) -> str:
    """A figure as Swedish text: grouped thousands, a decimal comma, one decimal below 10."""
    if x is None:
        return NO_VALUE
    x = float(x)
    decimals = 1 if abs(x) < 10 and x != int(x) else 0
    text = f"{abs(x):,.{decimals}f}".replace(",", THIN).replace(".", ",")
    return ("−" if x < 0 else "") + text


def signer_role(signoff: dict | None) -> str | None:
    """The role of a valid signoff ("name, role" → "role"); None for a draft."""
    if not signoff or not signoff.get("valid"):
        return None
    role = (signoff.get("by") or "").partition(",")[2].strip()
    return role or None


def meta_sv(meta: dict) -> str:
    run = meta["quarter"]
    parts = [f"{runs.label_sv(run)} {run}", f"metodversion {meta['method_version']}"]
    role = signer_role(meta.get("signoff"))
    parts.append(f"signerad av {role}" if role else "UTKAST")
    return " · ".join(parts)


def _cell(col: str, row: dict) -> str:
    v = row.get(col)
    if col == "level":
        return html.escape(LEVEL_SV.get(v, v or NO_VALUE))
    if col in NUMERIC:
        return number(v)
    return html.escape(str(v)) if v not in (None, "") else NO_VALUE


def render(table: list[dict], columns: list[str], meta: dict, publication) -> str:
    """index.html with every value from the publication, escaped."""
    cols = [(c, label) for c, label in COLUMNS if c in columns or c in ("name", "cell_id", "level", "customers")]
    num = ' class="num"'
    head = "".join(f'<th scope="col"{num if c in NUMERIC else ""}>{html.escape(label)}</th>' for c, label in cols)
    rows = "".join(
        "<tr>" + "".join((f'<th scope="row">{_cell(c, r)}</th>' if c == "name" else
                          f'<td class="num">{_cell(c, r)}</td>' if c in NUMERIC else f"<td>{_cell(c, r)}</td>")
                         for c, _ in cols) + "</tr>"
        for r in table)
    draft = meta["status"] != "signerad"
    run = meta["quarter"]
    tiles = (publication.map_tiles or "").strip()
    values = {
        "title": html.escape(f"Ledig kapacitet – {meta.get('dso') or meta['study']}"),
        "dso": html.escape(meta.get("dso") or meta["study"]),
        "meta": html.escape(meta_sv(meta)),
        "body_class": "is-draft" if draft else "is-signed",
        "draft_hidden": "" if draft else " hidden",
        "attribution": html.escape(publication.map_attribution.strip()) if tiles else "",
        "attribution_hidden": "" if tiles else " hidden",
        "caption": html.escape(f"Ledig kapacitet per cell, {runs.word_sv(run)} {run}. {len(table)} celler; "
                               f"{NO_VALUE} betyder inget värde (inte beräknat eller dolt)."),
        "head": head,
        "rows": rows,
        "footer": html.escape(f"Publicerad {meta['published_at']}."),
    }
    from .publish import render as fill

    return fill((SOURCE / "index.html").read_text(encoding="utf-8"), values)


def write(folder: Path, table: list[dict], columns: list[str], meta: dict, publication) -> list[str]:
    """folder/embed/: the rendered page and the two static files. Returns their paths, relative to
    the publication."""
    out = folder / "embed"
    out.mkdir(parents=True, exist_ok=True)
    write_text_atomic(out / "index.html", render(table, columns, meta, publication))
    for name in STATIC:
        shutil.copyfile(SOURCE / name, out / name)
    return [f"embed/{name}" for name in FILES]


def manifest_block(publication) -> dict:
    """What the manifest says about the embed: where it is and the one outside request it may make."""
    tiles = (publication.map_tiles or "").strip()
    return {"index": "embed/index.html", "map_tiles": tiles or None,
            "map_attribution": (publication.map_attribution.strip() or None) if tiles else None}


__all__ = ["COLUMNS", "FILES", "SOURCE", "manifest_block", "meta_sv", "number", "render", "signer_role", "write"]
