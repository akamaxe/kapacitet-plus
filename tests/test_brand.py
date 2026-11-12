"""The DSO's brand on the applicant's portal (kapacitet/brand.py): the settings' checks, each with its
code, the logo's checks without an image library, what /apply/brand exposes, and the seals.

Runs anywhere: the settings, the logo and the seals are in the test's own folder, and the PNGs are
built here from their chunks.
"""

from __future__ import annotations

import base64
import re
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def chunk(kind: bytes, data: bytes, crc: int | None = None) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF if crc is None else crc
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def png(width: int = 4, height: int = 3, extra: list[bytes] | None = None, signature: bytes = b"\x89PNG\r\n\x1a\n",
        idat: bytes | None = None) -> bytes:
    """A truecolour 8-bit PNG of width × height red pixels, with `extra` chunks after the IHDR."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xd0\x20\x20" * width for _ in range(max(height, 1)))
    data = zlib.compress(rows) if idat is None else idat
    return signature + chunk(b"IHDR", ihdr) + b"".join(extra or []) + chunk(b"IDAT", data) + chunk(b"IEND", b"")


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import dso
    from kapacitet.api.main import app

    monkeypatch.delenv("KAPACITET_DATABASE_URL", raising=False)
    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    with TestClient(app) as c:
        yield c, tmp_path / "requests"


def _code(r) -> str:
    return r.json()["detail"]["code"]


# ---------------------------------------------------------------- the settings

@pytest.mark.parametrize("change, code", [
    ({"brand_name": "N" * 81}, "brand_name_too_long"),
    ({"brand_contact": "c" * 201}, "brand_contact_too_long"),
    ({"brand_name": "Elnät\nAB"}, "brand_name_not_text"),
    ({"brand_contact": "Ring oss\x07"}, "brand_contact_not_text"),
    ({"brand_accent": "red"}, "brand_accent_format"),
    ({"brand_accent": "#12345"}, "brand_accent_format"),
    ({"brand_accent": "#123e7cff"}, "brand_accent_format"),
    # yellow on white, and a light grey: both under 4.5:1
    ({"brand_accent": "#ffd700"}, "brand_accent_contrast"),
    ({"brand_accent": "#8a939b"}, "brand_accent_contrast"),
])
def test_each_brand_field_is_refused_with_its_own_code(client, change, code):
    c, _ = client
    r = c.put("/api/v1/settings", json=change)
    assert r.status_code == 422 and _code(r) == code
    assert c.get("/api/v1/settings").json()["settings"]["brand_name"] == ""


def test_the_contrast_is_said_with_the_ratio_it_has_and_the_one_it_needs(client):
    from kapacitet import brand

    c, _ = client
    detail = c.put("/api/v1/settings", json={"brand_accent": "#ffd700"}).json()["detail"]
    assert detail["params"]["min"] == "4.5" and float(detail["params"]["ratio"]) < 4.5
    assert brand.contrast("#ffffff", "#000000") == pytest.approx(21.0)
    assert brand.accent_contrast("#123e7c") >= brand.CONTRAST_MIN


def test_the_brand_is_kept_normalised_and_the_change_is_sealed(client):
    from kapacitet import seals

    c, _ = client
    r = c.put("/api/v1/settings", json={"brand_name": "  Elnät Söder AB ", "brand_contact": "Kundtjänst 08-123 45 67\r\nvardagar 8-17",
                                        "brand_accent": "#7A1F2B"})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s["brand_name"] == "Elnät Söder AB" and s["brand_accent"] == "#7a1f2b"
    assert s["brand_contact"] == "Kundtjänst 08-123 45 67\nvardagar 8-17"
    changes = seals.sealed_of_kind("settings")
    assert len(changes) == 1 and changes[0]["record"]["after"]["brand_name"] == "Elnät Söder AB"
    assert changes[0]["record"]["before"]["brand_name"] == ""
    # emptied: the product's name and accent again
    assert c.put("/api/v1/settings", json={"brand_name": "", "brand_accent": ""}).status_code == 200
    b = c.get("/api/v1/apply/brand").json()
    assert b["name"] == "Kapacitet+" and b["accent"] is None and b["contact"].startswith("Kundtjänst")


# ---------------------------------------------------------------- the logo

@pytest.mark.parametrize("raw, status, code", [
    (b"GIF89a" + b"\x00" * 40, 422, "logo_not_png"),
    (png(signature=b"\x89PNG\r\n\x1a\x0a"[:7] + b"\x00"), 422, "logo_not_png"),
    (png(1025, 2), 422, "logo_bad_dimensions"),
    (png(3, 2000), 422, "logo_bad_dimensions"),
    (png(0, 2), 422, "logo_bad_dimensions"),
    (png(extra=[chunk(b"tEXt", b"Comment\x00hi", crc=1234)]), 422, "logo_bad_crc"),
    (png()[:-6], 422, "logo_malformed"),
    (png(extra=[chunk(b"ZZZZ", b"x")]), 422, "logo_malformed"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * (256 * 1024), 413, "logo_too_large"),
])
def test_a_logo_that_fails_a_check_is_refused_with_its_code(client, raw, status, code):
    c, folder = client
    r = c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(raw)})
    assert r.status_code == status and _code(r) == code
    assert not (folder / "brand-logo.png").exists()


def test_a_logo_that_is_not_base64_is_refused(client):
    c, _ = client
    r = c.put("/api/v1/settings/brand-logo", json={"png_base64": "not base64 at all!"})
    assert r.status_code == 422 and _code(r) == "logo_bad_base64"


def test_the_logo_keeps_only_its_image_chunks_and_is_sealed_by_its_digest(client):
    from kapacitet import brand, seals

    c, folder = client
    idat = zlib.compress(b"".join(b"\x00" + b"\x10\x60\xa0" * 4 for _ in range(3)))
    raw = png(extra=[chunk(b"tEXt", b"Author\x00Someone at home"), chunk(b"eXIf", b"MM\x00*GPS"),
                     chunk(b"gAMA", struct.pack(">I", 45455)), chunk(b"tRNS", b"\x00\x00\x00\x00\x00\x00")],
              idat=idat)
    r = c.put("/api/v1/settings/brand-logo", json={"png_base64": "data:image/png;base64," + b64(raw)})
    assert r.status_code == 200, r.text
    stored = (folder / "brand-logo.png").read_bytes()
    assert stored.startswith(brand.PNG_SIGNATURE) and chunk(b"IDAT", idat) in stored
    assert b"tEXt" not in stored and b"Someone" not in stored and b"eXIf" not in stored and b"gAMA" not in stored
    assert b"tRNS" in stored
    kinds = re.findall(rb"(IHDR|PLTE|tRNS|IDAT|IEND)", stored)
    assert kinds == [b"IHDR", b"tRNS", b"IDAT", b"IEND"]

    body = r.json()
    assert body["logo_sha256"] == brand.clean_png(stored).sha256 and body["logo_url"].startswith("/api/v1/apply/brand/logo.png")
    sealed = seals.sealed_of_kind("brand_logo")
    assert len(sealed) == 1
    rec = sealed[0]["record"]
    assert rec["before"] is None and rec["after"]["sha256"] == body["logo_sha256"] and rec["after"]["width"] == 4
    # the digest, never the bytes
    assert set(rec["after"]) == {"sha256", "bytes", "width", "height"}
    # the same logo again seals nothing
    assert c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(raw)}).status_code == 200
    assert len(seals.sealed_of_kind("brand_logo")) == 1


def test_the_logo_is_served_as_a_png_with_its_digest_and_removed(client):
    from kapacitet import seals

    c, folder = client
    assert c.get("/api/v1/apply/brand/logo.png").status_code == 404
    sha = c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(png())}).json()["logo_sha256"]
    r = c.get(c.get("/api/v1/apply/brand").json()["logo_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["etag"] == f'"{sha}"'
    assert r.content == (folder / "brand-logo.png").read_bytes()
    assert c.get("/api/v1/apply/brand/logo.png", headers={"If-None-Match": f'"{sha}"'}).status_code == 304

    gone = c.request("DELETE", "/api/v1/settings/brand-logo", json={})
    assert gone.status_code == 200 and gone.json()["logo_url"] is None
    assert not (folder / "brand-logo.png").exists()
    assert c.get("/api/v1/apply/brand/logo.png").status_code == 404
    last = seals.sealed_of_kind("brand_logo")[-1]["record"]
    assert last["before"]["sha256"] == sha and last["after"] is None


# ---------------------------------------------------------------- what the portal reads

def test_the_portal_reads_only_the_brand_and_nothing_else_of_the_settings(client):
    c, _ = client
    c.put("/api/v1/settings", json={"brand_name": "Elnät Söder AB", "brand_contact": "kundtjanst@example.se",
                                    "brand_accent": "#7a1f2b", "review_statement": "internal",
                                    "method_approval_reference": "Ei 2026-123"})
    c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(png())})
    b = c.get("/api/v1/apply/brand").json()
    assert set(b) == {"name", "contact", "accent", "logo_url", "logo_sha256"}
    assert b["name"] == "Elnät Söder AB" and b["accent"] == "#7a1f2b" and b["contact"] == "kundtjanst@example.se"
    assert "internal" not in str(b) and "Ei 2026-123" not in str(b)


def test_a_fresh_server_names_the_product(client):
    c, _ = client
    assert c.get("/api/v1/apply/brand").json() == {"name": "Kapacitet+", "contact": "", "accent": None,
                                                   "logo_url": None, "logo_sha256": None}


def test_the_contrast_is_checked_against_the_portals_own_colours():
    """brand.PORTAL_* are the light theme's --ground and --on-sl in tokens.css."""
    from kapacitet import brand

    css = (ROOT / "web" / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
    light = css.split(':root[data-theme="light"] {', 1)[1].split("}", 1)[0]
    token = lambda name: re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", light)[1].lower()  # noqa: E731
    assert brand.PORTAL_BACKGROUND == token("ground")
    assert brand.PORTAL_ON_ACCENT == token("on-sl")


# ---------------------------------------------------------------- order, stream and seal failures

def _plte() -> bytes:
    return chunk(b"PLTE", b"\x00\x00\x00")


def _raw_png(*chunks_: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks_)


_IHDR = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
_IDAT = chunk(b"IDAT", zlib.compress(b"\x00" + b"\xd0\x20\x20" * 2))


@pytest.mark.parametrize("raw", [
    _raw_png(_IHDR, _IDAT, _plte(), chunk(b"IEND", b"")),
    _raw_png(_IHDR, _IDAT, chunk(b"tEXt", b"a\x00b"), _IDAT, chunk(b"IEND", b"")),
    png(idat=b"this is not zlib"),
], ids=["plte_after_idat", "idat_not_consecutive", "idat_not_zlib"])
def test_chunk_order_and_the_image_stream_are_checked(client, raw):
    c, folder = client
    r = c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(raw)})
    assert r.status_code == 422 and _code(r) == "logo_malformed"
    assert not (folder / "brand-logo.png").exists()


def _image(width: int, height: int, depth: int = 8, colour: int = 2, interlace: int = 0, data: bytes | None = None) -> bytes:
    """A PNG of that header whose image data is `data` unpacked (all zeros of the right length by default)."""
    from kapacitet.brand import image_bytes

    ihdr = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace)
    body = bytes(image_bytes(width, height, depth, colour, interlace)) if data is None else data
    extra = [chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff")] if colour == 3 else []
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + b"".join(extra) + chunk(b"IDAT", zlib.compress(body))
            + chunk(b"IEND", b""))


def test_the_image_data_is_the_image_the_header_declares():
    """A 1 × 1 logo whose data unpacked to megabytes, a stream cut short and one too short for its
    size all passed: the stream was only unpacked up to a ceiling. It has to be the whole stream,
    exactly the image's bytes, and nothing after it."""
    from kapacitet.brand import BrandError, clean_png, image_bytes

    assert image_bytes(4, 3, 8, 2, 0) == 3 * (1 + 12)                 # three scanlines of four RGB pixels
    assert image_bytes(5, 1, 1, 0, 0) == 1 + 1                        # five 1-bit pixels pack into one byte
    assert image_bytes(1, 1, 8, 6, 1) == 1 + 4                        # interlaced: only the first pass has a pixel
    assert image_bytes(8, 8, 8, 0, 1) == 1 * 2 + 1 * 2 + 1 * 3 + 2 * 3 + 2 * 5 + 4 * 5 + 4 * 9
    for header in ((4, 3, 8, 2, 0), (16, 16, 8, 6, 0), (5, 2, 1, 0, 0), (7, 5, 4, 3, 0), (8, 8, 8, 0, 1),
                   (3, 3, 16, 4, 1), (1024, 2, 8, 2, 0)):
        assert clean_png(_image(*header)).width == header[0], header

    def refused(raw: bytes) -> bool:
        try:
            clean_png(raw)
        except BrandError as exc:
            return exc.code == "logo_malformed"
        return False

    bomb = _image(1, 1, data=bytes(4 * 1024 * 1024))                   # a few kilobytes that unpack to megabytes
    assert len(bomb) < 16 * 1024 and refused(bomb)
    assert refused(_image(16, 16, data=bytes(10)))                     # too short for 16 × 16
    assert refused(_image(4, 3, data=bytes(3 * 13 + 1)))               # one byte more than the image
    whole = zlib.compress(bytes(3 * 13))
    assert refused(png(idat=whole[:-4]))                               # the stream is cut short
    assert refused(png(idat=whole + b"trailing"))                      # bytes after the end of the stream
    assert not refused(png(idat=whole))


def test_an_oversize_text_gets_the_logo_code_not_a_generic_422(client):
    c, _ = client
    r = c.put("/api/v1/settings/brand-logo", json={"png_base64": "A" * 500_000})
    assert r.status_code == 413 and _code(r) == "logo_too_large"


def test_a_failed_seal_leaves_the_stored_logo_as_it_was(client, monkeypatch):
    from kapacitet import seals

    c, folder = client
    first, second = png(4, 3), png(5, 3)
    assert c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(first)}).status_code == 200
    stored = (folder / "brand-logo.png").read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(seals, "seal", boom)
    with pytest.raises(OSError):
        c.put("/api/v1/settings/brand-logo", json={"png_base64": b64(second)})
    assert (folder / "brand-logo.png").read_bytes() == stored
    with pytest.raises(OSError):
        c.request("DELETE", "/api/v1/settings/brand-logo", json={})
    assert (folder / "brand-logo.png").read_bytes() == stored
