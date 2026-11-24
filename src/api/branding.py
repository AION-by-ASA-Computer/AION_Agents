"""Public (unauthenticated) branding endpoints for chat-ui and admin-ui.

- ``GET /branding``: colours and asset URLs, fetched before login by the
  ``BrandingProvider`` of both frontends to theme the login pages too.
- ``GET /branding/assets/{kind}``: the logo / favicon bytes.

Branding is not secret (it is what every visitor of the login page sees), so
no auth is required. The asset endpoint is hardened because it serves
admin-uploaded content from the API origin: ``nosniff`` plus, for SVG, a CSP
that sandboxes the document and blocks scripts if the URL is opened directly.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response

from src.branding.images import ASSET_KINDS
from src.branding.service import get_asset, get_branding, public_view

logger = logging.getLogger("aion.api.branding")
router = APIRouter(prefix="/branding", tags=["branding"])

# Applied to every asset; SVG additionally gets _SVG_CSP.
_SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def _tenant_id() -> str:
    return (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip()


@router.get("")
async def get_public_branding(response: Response) -> Dict[str, Any]:
    """Tenant branding for the UIs. Falls back to "all defaults" if the DB is unreachable,
    so a branding problem can never block the login page."""
    # Short browser cache: admins see their change within a minute even
    # without the explicit cache reset done by the admin-ui after a save.
    response.headers["Cache-Control"] = "public, max-age=60"
    try:
        return public_view(await get_branding(_tenant_id()))
    except Exception:  # noqa: BLE001 - defensive: branding must never break login
        logger.exception("branding lookup failed; serving defaults")
        response.headers["Cache-Control"] = "no-store"
        return {
            "primary_color_light": None,
            "primary_color_dark": None,
            "logo_light": None,
            "logo_dark": None,
            "favicon": None,
            "custom_logo": False,
        }


@router.get("/assets/{kind}")
async def get_branding_asset(
    kind: str, request: Request, v: Optional[str] = None
) -> Response:
    """Serve a branding image.

    URLs produced by ``GET /branding`` carry ``?v=<hash prefix>``: when it
    matches the stored content the response is cacheable forever (a new
    upload changes the URL). Without/with a stale ``v`` it is revalidated via ETag.
    """
    if kind not in ASSET_KINDS:
        raise HTTPException(404, detail={"code": "not_found"})
    row = await get_asset(_tenant_id(), kind)
    if row is None:
        raise HTTPException(404, detail={"code": "not_found"})

    etag = f'"{row.sha256}"'
    headers = {
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    if v and row.sha256.startswith(v):
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "public, no-cache"
    if row.mime == "image/svg+xml":
        headers["Content-Security-Policy"] = _SVG_CSP

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=row.data, media_type=row.mime, headers=headers)
