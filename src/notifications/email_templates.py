"""Template email (``templates/<lang>/<kind>.txt|.html``).

Il file ``.txt`` ha l'oggetto sulla prima riga, una riga vuota e poi il corpo.
Le variabili usano la sintassi ``string.Template`` (``$link``, ``${name}``);
nell'HTML i valori sono sottoposti a escape.

Branding (``src/branding/service.py``): with a raster custom logo the layout
shows it at the top as ``cid:`` inline image (the MIME part is added by
``mailer.build_message``; no public URL is needed, so it works in intranet
deployments too). With any custom logo the footer carries the mandatory
"Powered by AION" link. The button colour follows the light primary colour.
"""

from __future__ import annotations

import html
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from .smtp_settings import EMAIL_LANGUAGES

if TYPE_CHECKING:
    from src.branding.service import EmailBranding

_DIR = Path(__file__).resolve().parent / "templates"

KINDS = ("invite", "reset", "migration", "test")

# Content-ID of the inline logo; must match mailer.LOGO_CID.
LOGO_CID = "aion-brand-logo"
# Default button colours (the pre-branding look).
_DEFAULT_BUTTON_BG = "#18181b"
_DEFAULT_BUTTON_FG = "#ffffff"
_POWERED_BY_HTML = (
    '<p style="font-size:11px;color:#a1a1aa;margin-top:4px;">Powered by '
    '<a href="https://aion-asa.com" style="color:#71717a;text-decoration:underline;">AION</a></p>'
)
_POWERED_BY_TEXT = "\n\n--\nPowered by AION - https://aion-asa.com\n"


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of ``#rrggbb``."""
    chans = []
    for i in (1, 3, 5):
        c = int(hex_color[i : i + 2], 16) / 255
        chans.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = chans
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_text_color(bg_hex: str) -> str:
    """White or near-black, whichever has the higher WCAG contrast on ``bg_hex``."""
    lum = _relative_luminance(bg_hex)
    contrast_white = 1.05 / (lum + 0.05)
    contrast_black = (lum + 0.05) / 0.05
    return "#ffffff" if contrast_white >= contrast_black else "#18181b"


def render(
    kind: str,
    lang: str,
    values: Dict[str, str],
    branding: Optional["EmailBranding"] = None,
) -> Tuple[str, str, str]:
    """Ritorna ``(subject, text, html)``.

    ``branding`` (optional) adds the tenant logo, button colour and the
    "Powered by AION" footer; ``None`` keeps the default AION look.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown template: {kind}")
    lang = lang if lang in EMAIL_LANGUAGES else "it"
    raw = (_DIR / lang / f"{kind}.txt").read_text(encoding="utf-8")
    subject_tpl, _, body_tpl = raw.partition("\n\n")
    subject = Template(subject_tpl.strip()).safe_substitute(values)
    text = Template(body_tpl).safe_substitute(values)
    escaped = {k: html.escape(v, quote=True) for k, v in values.items()}
    # Button colours are computed here (not caller input); the colour comes
    # from normalize_color() so it is always a strict #rrggbb.
    button_bg = (branding.button_color if branding else None) or _DEFAULT_BUTTON_BG
    escaped["brand_bg"] = button_bg
    escaped["brand_fg"] = (
        readable_text_color(button_bg)
        if button_bg != _DEFAULT_BUTTON_BG
        else _DEFAULT_BUTTON_FG
    )
    html_tpl = (_DIR / lang / f"{kind}.html").read_text(encoding="utf-8")
    body_html = Template(html_tpl).safe_substitute(escaped)
    layout = (_DIR / "layout.html").read_text(encoding="utf-8")
    logo_html = ""
    if branding and branding.logo_bytes:
        logo_html = (
            '<tr><td style="padding-bottom:24px;">'
            f'<img src="cid:{LOGO_CID}" alt="{escaped.get("platform", "")}" '
            'style="display:block;max-height:48px;max-width:220px;height:auto;border:0;">'
            "</td></tr>\n"
        )
    custom = bool(branding and branding.custom_logo)
    page = Template(layout).safe_substitute(
        subject=html.escape(subject),
        body=body_html,
        platform=escaped.get("platform", ""),
        logo=logo_html,
        powered_by=_POWERED_BY_HTML if custom else "",
    )
    if custom:
        text = text.rstrip("\n") + _POWERED_BY_TEXT
    return subject, text, page
