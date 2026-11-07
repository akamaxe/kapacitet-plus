"""The DSO's brand on the applicant's portal: its name, how to reach it, its accent and its logo.

A household meets the portal inside the DSO's own channel, so the portal speaks under the DSO's
name; Kapacitet+ is named only small in its footer. The three texts are DSO settings
(kapacitet/dso.py: `brand_name`, `brand_contact`, `brand_accent`), checked here so each refusal
has its own code, and sealed with every other change of the settings.

The accent is either empty (the product's own) or `#RRGGBB` legible where the portal uses it: at
least 4.5:1 (WCAG 2 AA for body text) against the portal's background, as a link, and against the
text drawn on it, as a button. Both colours are the light theme's (web/src/styles/tokens.css,
`--ground` and `--on-sl`); tests/test_brand.py holds them to that file. At night the portal keeps
the product's lifted accent, since no colour reads at 4.5:1 on both a white and a black ground.

The logo is a PNG of at most 256 kB and 1024 pixels a side, checked without an image library:
the signature, the IHDR and every chunk's CRC, then rebuilt from its chunks keeping only IHDR,
PLTE, tRNS, IDAT and IEND, so text, EXIF, colour profiles and every other ancillary chunk are
dropped before anything is stored. It is kept beside the settings (data/requests/brand-logo.png)
or in the database, and sealed by its digest, never its bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

PRODUCT_NAME = "Kapacitet+"
NAME_MAX = 80
CONTACT_MAX = 200
LOGO_MAX_BYTES = 256 * 1024
LOGO_MAX_SIDE = 1024
CONTRAST_MIN = 4.5
# the portal's light theme (web/src/styles/tokens.css): the page's ground and the text on the accent
PORTAL_BACKGROUND = "#ffffff"
PORTAL_ON_ACCENT = "#ffffff"
ACCENT = re.compile(r"^#[0-9a-fA-F]{6}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEPT_CHUNKS = (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND")
# the bit depths each PNG colour type allows
_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_LOCK = threading.Lock()

ERRORS = {
    "brand_name_too_long": "the name on the portal is longer than {max} characters",
    "brand_contact_too_long": "the contact text is longer than {max} characters",
    "brand_name_not_text": "the name on the portal is one line of plain text",
    "brand_contact_not_text": "the contact text holds characters that are not plain text",
    "brand_accent_format": "the accent is empty or a colour written #RRGGBB",
    "brand_accent_contrast": "the accent {value} has a contrast of {ratio}:1 on the portal; it needs at least {min}:1",
    "logo_bad_base64": "the logo is not valid base64",
    "logo_too_large": "the logo is larger than {kb} kB",
    "logo_not_png": "the logo is not a PNG file",
    "logo_bad_dimensions": "the logo is {width} × {height} pixels; each side has to be between 1 and {max}",
    "logo_bad_crc": "the logo's {chunk} chunk fails its CRC check",
    "logo_malformed": "the logo's PNG chunks are broken or incomplete",
    "logo_missing": "no logo has been set",
}


class BrandError(ValueError):
    """A refusal with its code (ERRORS) and parameters."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


# ---------------------------------------------------------------- the texts and the accent

def _luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance of #RRGGBB."""
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two #RRGGBB colours, from 1 to 21."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def accent_contrast(value: str) -> float:
    """The weaker of the accent's two contrasts on the portal: as a link on the ground, and under
    the text of a button."""
    return min(contrast(value, PORTAL_BACKGROUND), contrast(value, PORTAL_ON_ACCENT))


def _text(field: str, value: str, most: int, lines: bool) -> str:
    value = value.strip()
    if len(value) > most:
        raise BrandError(f"{field}_too_long", max=most)
    allowed = "\n" if lines else ""
    if any((ord(ch) < 32 and ch not in allowed) or 0x7F <= ord(ch) < 0xA0 or ch in "  " for ch in value):
        raise BrandError(f"{field}_not_text")
    return value


def check_name(value: str) -> str:
    """The DSO's name as the portal shows it: one line of at most NAME_MAX characters."""
    return _text("brand_name", value, NAME_MAX, lines=False)


def check_contact(value: str) -> str:
    """How to reach the DSO's customer service: plain text of at most CONTACT_MAX characters."""
    return _text("brand_contact", value.replace("\r\n", "\n"), CONTACT_MAX, lines=True)


def check_accent(value: str) -> str:
    """Empty, or #RRGGBB (kept in lower case) legible on the portal."""
    value = value.strip()
    if not value:
        return ""
    if not ACCENT.match(value):
        raise BrandError("brand_accent_format")
    ratio = accent_contrast(value)
    if ratio < CONTRAST_MIN:
        raise BrandError("brand_accent_contrast", value=value.lower(), ratio=f"{int(ratio * 100) / 100:.2f}",
                         min=f"{CONTRAST_MIN:.1f}")
    return value.lower()


def check_settings(change: dict) -> dict:
    """The brand fields of a settings change, checked and normalised; the others as given."""
    out = dict(change)
    for key, check in (("brand_name", check_name), ("brand_contact", check_contact), ("brand_accent", check_accent)):
        if out.get(key) is not None:
            out[key] = check(out[key])
    return out


# ---------------------------------------------------------------- the logo

@dataclass(frozen=True)
class Logo:
    png: bytes
    sha256: str
    width: int
    height: int


def decode(png_base64: str) -> bytes:
    """The bytes of a base64 text (a data: URL's prefix is allowed), within LOGO_MAX_BYTES."""
    text = png_base64.strip()
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    # base64 is 4/3 of the bytes: a text far beyond the limit is refused before it is decoded
    if len(text) > (LOGO_MAX_BYTES * 4) // 3 + 8:
        raise BrandError("logo_too_large", kb=LOGO_MAX_BYTES // 1024)
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except (binascii.Error, ValueError):
        raise BrandError("logo_bad_base64") from None
    if len(raw) > LOGO_MAX_BYTES:
        raise BrandError("logo_too_large", kb=LOGO_MAX_BYTES // 1024)
    return raw


def _chunks(raw: bytes):
    """(type, data) of each chunk after the signature, its length and CRC checked."""
    pos = len(PNG_SIGNATURE)
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise BrandError("logo_malformed")
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 8 + length
        if length > 2**31 - 1 or end + 4 > len(raw) or not kind.isalpha():
            raise BrandError("logo_malformed")
        data = raw[pos + 8:end]
        (crc,) = struct.unpack(">I", raw[end:end + 4])
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise BrandError("logo_bad_crc", chunk=kind.decode("ascii"))
        yield kind, data
        pos = end + 4


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def clean_png(raw: bytes) -> Logo:
    """The PNG checked and rebuilt from its kept chunks (KEPT_CHUNKS), or a BrandError."""
    if len(raw) > LOGO_MAX_BYTES:
        raise BrandError("logo_too_large", kb=LOGO_MAX_BYTES // 1024)
    if not raw.startswith(PNG_SIGNATURE):
        raise BrandError("logo_not_png")
    chunks = list(_chunks(raw))
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise BrandError("logo_malformed")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if not (0 < width <= LOGO_MAX_SIDE and 0 < height <= LOGO_MAX_SIDE):
        raise BrandError("logo_bad_dimensions", width=width, height=height, max=LOGO_MAX_SIDE)
    if depth not in _DEPTHS.get(colour, ()) or compression or filtering or interlace not in (0, 1):
        raise BrandError("logo_malformed")
    kinds = [k for k, _ in chunks]
    # an unknown critical chunk (upper-case first letter) is one a decoder may not skip
    if any(k[:1].isupper() and k not in KEPT_CHUNKS for k in kinds):
        raise BrandError("logo_malformed")
    if kinds[-1] != b"IEND" or kinds.count(b"IEND") != 1 or kinds.count(b"IHDR") != 1 or b"IDAT" not in kinds:
        raise BrandError("logo_malformed")
    if colour == 3 and b"PLTE" not in kinds:
        raise BrandError("logo_malformed")
    first = kinds.index(b"IDAT")
    idats = [i for i, k in enumerate(kinds) if k == b"IDAT"]
    if idats != list(range(first, first + len(idats))):
        raise BrandError("logo_malformed")  # IDAT chunks must be consecutive
    if b"PLTE" in kinds and kinds.index(b"PLTE") > first:
        raise BrandError("logo_malformed")  # the palette comes before the image data
    try:
        zlib.decompressobj().decompress(b"".join(d for k, d in chunks if k == b"IDAT"),
                                        width * height * 8 + height + 1024)
    except zlib.error:
        raise BrandError("logo_malformed") from None
    kept = b"".join(_chunk(k, d) for k, d in chunks if k in KEPT_CHUNKS)
    png = PNG_SIGNATURE + kept
    return Logo(png=png, sha256=hashlib.sha256(png).hexdigest(), width=width, height=height)


def logo_store():
    """Where the logo is kept: beside settings.json, or in the database (kapacitet.store)."""
    from . import dso, store

    return store.brand_logo(dso.path().parent / "brand-logo.png")


def load_logo() -> Logo | None:
    """The logo in force, or None; a stored file that no longer passes the checks counts as none."""
    raw = logo_store().get()
    if raw is None:
        return None
    try:
        return clean_png(raw)
    except BrandError:
        return None


def _seal_logo(before: Logo | None, after: Logo | None) -> None:
    from . import seals

    stamp = datetime.now(timezone.utc)
    side = lambda lg: None if lg is None else {"sha256": lg.sha256, "bytes": len(lg.png),  # noqa: E731
                                                "width": lg.width, "height": lg.height}
    seals.seal("brand_logo", stamp.strftime("%Y%m%dT%H%M%S%fZ"),
               {"changed_at": stamp.isoformat(), "before": side(before), "after": side(after)})


def set_logo(png_base64: str) -> Logo:
    """Check, clean, keep and seal a new logo (by its digest); the same logo again changes nothing."""
    logo = clean_png(decode(png_base64))
    blob = logo_store()
    with _LOCK, blob.locked():
        before = load_logo()
        if before is not None and before.sha256 == logo.sha256:
            return before
        blob.put(logo.png)
        try:
            _seal_logo(before, logo)
        except Exception:
            # no seal, no change: put the previous logo back
            if before is not None:
                blob.put(before.png)
            else:
                blob.delete()
            raise
    return logo


def remove_logo() -> None:
    """Remove the logo, sealing what it was; nothing to remove changes nothing."""
    blob = logo_store()
    with _LOCK, blob.locked():
        before = load_logo()
        if before is None and blob.get() is None:
            return
        raw = blob.get()
        blob.delete()
        try:
            _seal_logo(before, None)
        except Exception:
            if raw is not None:
                blob.put(raw)
            raise


# ---------------------------------------------------------------- what the portal reads

def public(settings=None, logo: Logo | None | bool = True) -> dict:
    """GET /apply/brand: the name (the product's when none is set), the contact, the accent and the
    logo's address and digest; nothing else of the DSO's settings."""
    from . import dso

    s = settings or dso.load()
    lg = load_logo() if logo is True else (logo or None)
    return {
        "name": s.brand_name or PRODUCT_NAME,
        "contact": s.brand_contact,
        "accent": s.brand_accent or None,
        "logo_url": None if lg is None else f"/api/v1/apply/brand/logo.png?v={lg.sha256[:16]}",
        "logo_sha256": None if lg is None else lg.sha256,
    }
