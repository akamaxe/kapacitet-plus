"""A minimal DOCX writer, with the standard library only (task 2.6 of the technical review).

A DSO files its connection studies as documents. This writes WordprocessingML a word processor
opens (Word, LibreOffice): a title, headings, paragraphs, tables with a header row, and a header
and a footer on every page. Nothing else, and no dependency: the package is a zip of a handful of
XML parts, written in one go with zipfile.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _text(text: str) -> str:
    return escape(str(text), {'"': "&quot;"})


def _run(text: str, bold: bool = False, italic: bool = False, size: int | None = None) -> str:
    props = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    lines = str(text).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{_text(line)}</w:t>' for line in lines)
    return f"<w:r>{rpr}{body}</w:r>"


def _para(text: str, style: str | None = None, **run) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_run(text, **run)}</w:p>"


class Document:
    """Blocks in order; `to_bytes` writes the package."""

    def __init__(self, title: str, header: str = "", footer: str = "", language: str = "sv-SE"):
        self.title, self.header, self.footer, self.language = title, header, footer, language
        self.blocks: list[str] = [_para(title, "Title")]

    def heading(self, text: str, level: int = 1) -> "Document":
        self.blocks.append(_para(text, f"Heading{max(1, min(level, 3))}"))
        return self

    def paragraph(self, text: str, italic: bool = False, bold: bool = False) -> "Document":
        self.blocks.append(_para(text, None, italic=italic, bold=bold))
        return self

    def table(self, header: list[str], rows: list[list[object]]) -> "Document":
        def cell(value: object, bold: bool = False) -> str:
            return (f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
                    f"{_para('–' if value is None else value, None, bold=bold)}</w:tc>")

        head = "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(cell(h, True) for h in header) + "</w:tr>"
        body = "".join("<w:tr>" + "".join(cell(v) for v in row) + "</w:tr>" for row in rows)
        self.blocks.append('<w:tbl><w:tblPr><w:tblStyle w:val="Table"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr>'
                           f"{head}{body}</w:tbl>")
        self.blocks.append("<w:p/>")
        return self

    # ------------------------------------------------------------------ the package

    def _document(self) -> str:
        sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
                '<w:footerReference w:type="default" r:id="rIdFooter"/>'
                '<w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="709" w:footer="709" '
                'w:gutter="0"/></w:sectPr>')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W} {R}><w:body>'
                + "".join(self.blocks) + sect + "</w:body></w:document>")

    def _styles(self) -> str:
        def style(sid: str, name: str, size: int, bold: bool = True, space: int = 240) -> str:
            return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
                    f'<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{space}" w:after="120"/>'
                    f'</w:pPr><w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr></w:style>')

        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
                f'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
                f'<w:sz w:val="21"/><w:lang w:val="{self.language}"/></w:rPr></w:rPrDefault>'
                '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
                '</w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                + style("Title", "Title", 40, space=0) + style("Heading1", "heading 1", 30)
                + style("Heading2", "heading 2", 25) + style("Heading3", "heading 3", 22)
                + '<w:style w:type="table" w:styleId="Table"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
                + "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                          for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
                + '</w:tblBorders><w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>'
                '</w:tblCellMar></w:tblPr></w:style></w:styles>')

    def _margin(self, kind: str, text: str) -> str:
        tag = "hdr" if kind == "header" else "ftr"
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:{tag} {W} {R}>'
                f"{_para(text, None, size=16)}</w:{tag}>")

    def to_bytes(self) -> bytes:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
                '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                "</Types>"),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                "</Relationships>"),
            "word/_rels/document.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                "</Relationships>"),
            "word/document.xml": self._document(),
            "word/styles.xml": self._styles(),
            "word/header1.xml": self._margin("header", self.header),
            "word/footer1.xml": self._margin("footer", self.footer),
            "docProps/core.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f"<dc:title>{_text(self.title)}</dc:title><dc:creator>Kapacitet+</dc:creator>"
                f'<dc:language>{self.language}</dc:language>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>'),
            "docProps/app.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                "<Application>Kapacitet+</Application></Properties>"),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in parts.items():
                z.writestr(name, text.encode("utf-8"))
        return buf.getvalue()


def text_of(data: bytes) -> str:
    """Every paragraph's text of a DOCX this module wrote, one per line: what a test reads back."""
    import re

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    from xml.sax.saxutils import unescape

    out = []
    for para in re.findall(r"<w:p>(.*?)</w:p>", xml, flags=re.S):
        out.append(unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.S)), {"&quot;": '"'}))
    return "\n".join(out)
