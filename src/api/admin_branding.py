"""Admin API: tenant branding (logo light/dark, favicon, primary colours).

Prefix ``/admin/branding``, admin role required. Used by admin-ui
(Settings -> Brand, ``admin-ui/components/settings/BrandingSection.tsx``).
The public read side lives in ``src/api/branding.py``.

"Powered by AION" is not a setting: the UIs and emails always show it while
a custom logo is active (contractual attribution), so nothing here can turn it off.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.api.auth_login import ChatAuthIdentity, require_admin_role
from src.branding.images import ASSET_KINDS, BrandingImageError, max_bytes_for
from src.branding.service import (
    admin_view,
    delete_asset,
    get_branding,
    normalize_color,
    put_asset,
    upsert_colors,
)
from src.data.audit import record_admin_action

logger = logging.getLogger("aion.api.admin_branding")
router = APIRouter(prefix="/admin/branding", tags=["admin_branding"])


def _tenant_id() -> str:
    return (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip()


class BrandingColorsBody(BaseModel):
    """Both colours as ``#rrggbb``; empty/null restores the AION default for that theme."""

    primary_color_light: Optional[str] = Field(None, max_length=16)
    primary_color_dark: Optional[str] = Field(None, max_length=16)


@router.get("")
async def get_admin_branding(
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    return admin_view(await get_branding(_tenant_id()))


@router.put("")
async def put_admin_branding(
    body: BrandingColorsBody,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Save the two primary colours."""
    try:
        light = normalize_color(body.primary_color_light)
    except ValueError:
        raise HTTPException(
            400, detail={"code": "invalid_color", "field": "primary_color_light"}
        )
    try:
        dark = normalize_color(body.primary_color_dark)
    except ValueError:
        raise HTTPException(
            400, detail={"code": "invalid_color", "field": "primary_color_dark"}
        )

    tid = _tenant_id()
    await upsert_colors(
        tid,
        primary_color_light=light,
        primary_color_dark=dark,
        actor_user_id=auth.user_row_id,
    )
    await record_admin_action(
        tenant_id=tid,
        actor=auth.identifier,
        operation="branding.update",
        resource_type="branding_settings",
        resource_id=tid,
        payload={"primary_color_light": light, "primary_color_dark": dark},
    )
    logger.info("Branding colours updated by %s", auth.identifier)
    return admin_view(await get_branding(tid))


@router.put("/assets/{kind}")
async def put_admin_branding_asset(
    kind: str,
    file: UploadFile = File(...),
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Upload (or replace) ``logo_light``, ``logo_dark`` or ``favicon``."""
    if kind not in ASSET_KINDS:
        raise HTTPException(404, detail={"code": "invalid_kind"})
    # Bounded read: one byte over the limit is enough to reject without
    # buffering an arbitrarily large body.
    limit = max_bytes_for(kind)
    data = await file.read(limit + 1)

    tid = _tenant_id()
    try:
        meta = await put_asset(tid, kind, data, actor_user_id=auth.user_row_id)
    except BrandingImageError as e:
        raise HTTPException(e.status, detail={"code": e.code, "max_bytes": limit})

    await record_admin_action(
        tenant_id=tid,
        actor=auth.identifier,
        operation="branding.asset.put",
        resource_type="branding_asset",
        resource_id=kind,
        # Metadata only: never the image bytes.
        payload={
            "kind": kind,
            "mime": meta.mime,
            "size": meta.size,
            "sha256": meta.sha256,
        },
    )
    logger.info(
        "Branding asset %s uploaded by %s (%d bytes)", kind, auth.identifier, meta.size
    )
    return admin_view(await get_branding(tid))


@router.delete("/assets/{kind}")
async def delete_admin_branding_asset(
    kind: str,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Remove an image (the UIs fall back to the AION logo / favicon)."""
    if kind not in ASSET_KINDS:
        raise HTTPException(404, detail={"code": "invalid_kind"})
    tid = _tenant_id()
    removed = await delete_asset(tid, kind)
    if removed:
        await record_admin_action(
            tenant_id=tid,
            actor=auth.identifier,
            operation="branding.asset.delete",
            resource_type="branding_asset",
            resource_id=kind,
            payload={"kind": kind},
        )
    return admin_view(await get_branding(tid))
