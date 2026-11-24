"""Validation of uploaded branding images (logo / favicon).

Used by ``src/branding/service.py`` before anything reaches the DB. The type
is detected from the file's magic bytes and never from the client-supplied
Content-Type, because the bytes are later served back from a public,
unauthenticated endpoint: a mislabelled HTML file must never be stored.

SVG is accepted because most company logos are vector. It is served with a
sandboxing CSP (see ``src/api/branding.py``) and rendered by the UIs only via
``<img>``, where scripts never run; the content checks below are defence in
depth against someone opening the asset URL directly.
"""

from __future__ import annotations

import re
from typing import Optional

# Asset kinds and the formats each accepts.
LOGO_KINDS = ("logo_light", "logo_dark")
ASSET_KINDS = LOGO_KINDS + ("favicon",)

_LOGO_MIMES = ("image/png", "image/jpeg", "image/webp", "image/svg+xml")
_FAVICON_MIMES = ("image/png", "image/x-icon", "image/svg+xml")

# Size limits: a logo is a few tens of KB in practice, 1 MB leaves headroom for
# high-resolution PNGs; a favicon has no reason to exceed 256 KB.
LOGO_MAX_BYTES = 1024 * 1024
FAVICON_MAX_BYTES = 256 * 1024

# Raster formats usable as an inline email image (SVG is blocked by most clients).
EMAIL_SAFE_MIMES = ("image/png", "image/jpeg")

# Markers of active content inside an SVG. ``on\w+=`` catches event handlers
# (onload=, onclick=, ...), ``href`` with ``javascript:`` catches link payloads,
# ``foreignObject`` can embed arbitrary HTML, ``<!ENTITY`` can trigger XXE.
_SVG_FORBIDDEN = re.compile(
    rb"<\s*script|<\s*foreignObject|\bon[a-z]+\s*=|javascript\s*:|<!ENTITY|<\s*iframe|<\s*embed|<\s*object",
    re.IGNORECASE,
)


class BrandingImageError(ValueError):
    """Rejected upload. ``code`` is the stable API error code, ``status`` the HTTP status."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def max_bytes_for(kind: str) -> int:
    """Upload limit (bytes) for the given asset kind."""
    return FAVICON_MAX_BYTES if kind == "favicon" else LOGO_MAX_BYTES


def sniff_mime(data: bytes) -> Optional[str]:
    """Return the image MIME type recognised from the magic bytes, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    # SVG is text: skip an optional BOM / XML prolog / comments and look for
    # the root element within the first KB.
    head = data[:1024].lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<") and re.search(rb"<svg[\s>]", head, re.IGNORECASE):
        return "image/svg+xml"
    return None


def validate_image(kind: str, data: bytes) -> str:
    """Validate an upload for ``kind`` and return its detected MIME type.

    Raises :class:`BrandingImageError` with codes ``invalid_kind``,
    ``empty_file``, ``too_large`` (413), ``unsupported_type`` (415) or
    ``unsafe_svg``.
    """
    if kind not in ASSET_KINDS:
        raise BrandingImageError("invalid_kind", 404)
    if not data:
        raise BrandingImageError("empty_file", 422)
    if len(data) > max_bytes_for(kind):
        raise BrandingImageError("too_large", 413)
    mime = sniff_mime(data)
    allowed = _FAVICON_MIMES if kind == "favicon" else _LOGO_MIMES
    if mime is None or mime not in allowed:
        raise BrandingImageError("unsupported_type", 415)
    if mime == "image/svg+xml":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise BrandingImageError("unsupported_type", 415)
        if _SVG_FORBIDDEN.search(data):
            raise BrandingImageError("unsafe_svg", 400)
    return mime
