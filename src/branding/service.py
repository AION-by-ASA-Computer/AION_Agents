"""Read/write access to the tenant branding (tables ``branding_settings`` and ``branding_assets``).

Used by the public router (``src/api/branding.py``), the admin router
(``src/api/admin_branding.py``) and the email renderer
(``src/notifications/email_templates.py``). Mirrors the SMTP settings module:
a short in-process cache (the backend runs a single worker) invalidated after
every write done here.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, select

from src.data.engine import get_async_session_maker
from src.data.models import BrandingAsset, BrandingSettings
from src.branding.images import ASSET_KINDS, EMAIL_SAFE_MIMES, validate_image

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Cache of the public view, keyed by tenant: {tenant_id: (expiry_ts, snapshot)}.
_CACHE: Dict[str, tuple] = {}
CACHE_TTL = 15.0  # seconds; same trade-off as smtp_settings


@dataclass(frozen=True)
class AssetMeta:
    """Metadata of a stored image (no bytes)."""

    mime: str
    sha256: str
    size: int
    updated_at: Optional[str]


@dataclass(frozen=True)
class BrandingSnapshot:
    """Resolved branding of a tenant; ``None`` fields mean "AION default"."""

    primary_color_light: Optional[str]
    primary_color_dark: Optional[str]
    assets: Dict[str, AssetMeta]
    updated_at: Optional[str]

    @property
    def custom_logo(self) -> bool:
        """True when a custom logo replaces the AION one (=> "Powered by AION" is mandatory)."""
        return "logo_light" in self.assets or "logo_dark" in self.assets


def invalidate_cache(tenant_id: str) -> None:
    """Drop the cached snapshot after a write."""
    _CACHE.pop(tenant_id, None)


def normalize_color(value: Optional[str]) -> Optional[str]:
    """Return ``#rrggbb`` lower-case, ``None`` for empty input.

    Raises ``ValueError`` for anything that is not a 6-digit hex colour: the
    value ends up inside a CSS rule and an email ``style`` attribute, so only
    this strict shape is allowed.
    """
    v = (value or "").strip()
    if not v:
        return None
    if not _HEX_COLOR.match(v):
        raise ValueError("invalid_color")
    return v.lower()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


async def get_branding(tenant_id: str) -> BrandingSnapshot:
    """Load the tenant branding (cached ``CACHE_TTL`` seconds). Never raises on a missing row."""
    now = time.time()
    cached = _CACHE.get(tenant_id)
    if cached and now < cached[0]:
        return cached[1]

    async with get_async_session_maker()() as session:
        row = await session.get(BrandingSettings, tenant_id)
        # Select metadata only: the image bytes are loaded on demand by get_asset().
        res = await session.execute(
            select(
                BrandingAsset.kind,
                BrandingAsset.mime,
                BrandingAsset.sha256,
                BrandingAsset.size,
                BrandingAsset.updated_at,
            ).where(BrandingAsset.tenant_id == tenant_id)
        )
        assets = {
            kind: AssetMeta(mime=mime, sha256=sha, size=int(size), updated_at=_iso(ts))
            for kind, mime, sha, size, ts in res.all()
            if kind in ASSET_KINDS
        }

    snap = BrandingSnapshot(
        primary_color_light=row.primary_color_light if row else None,
        primary_color_dark=row.primary_color_dark if row else None,
        assets=assets,
        updated_at=_iso(row.updated_at) if row else None,
    )
    _CACHE[tenant_id] = (now + CACHE_TTL, snap)
    return snap


def asset_url(kind: str, meta: AssetMeta) -> str:
    """Path (relative to the API base) of a public asset, versioned by content hash."""
    return f"/branding/assets/{kind}?v={meta.sha256[:12]}"


def public_view(snap: BrandingSnapshot) -> Dict[str, Any]:
    """Shape returned by ``GET /branding`` and embedded in the admin view."""

    def _asset(kind: str) -> Optional[Dict[str, Any]]:
        meta = snap.assets.get(kind)
        if meta is None:
            return None
        return {"url": asset_url(kind, meta), "mime": meta.mime}

    return {
        "primary_color_light": snap.primary_color_light,
        "primary_color_dark": snap.primary_color_dark,
        "logo_light": _asset("logo_light"),
        "logo_dark": _asset("logo_dark"),
        "favicon": _asset("favicon"),
        # The UIs show "Powered by AION" whenever this is true; not configurable.
        "custom_logo": snap.custom_logo,
    }


def admin_view(snap: BrandingSnapshot) -> Dict[str, Any]:
    """Admin view: the public fields plus sizes and timestamps for the settings page."""
    out = public_view(snap)
    for kind in ASSET_KINDS:
        meta = snap.assets.get(kind)
        if out[kind] is not None and meta is not None:
            out[kind].update(
                {
                    "size": meta.size,
                    "updated_at": meta.updated_at,
                    "sha256": meta.sha256,
                }
            )
    out["updated_at"] = snap.updated_at
    # Only raster logos can be embedded in emails; the admin UI warns otherwise.
    light = snap.assets.get("logo_light") or snap.assets.get("logo_dark")
    out["email_logo"] = bool(light and light.mime in EMAIL_SAFE_MIMES)
    return out


async def upsert_colors(
    tenant_id: str,
    *,
    primary_color_light: Optional[str],
    primary_color_dark: Optional[str],
    actor_user_id: Optional[str],
) -> None:
    """Save both colours (already normalised; ``None`` = AION default)."""
    async with get_async_session_maker()() as session:
        row = await session.get(BrandingSettings, tenant_id)
        if row is None:
            row = BrandingSettings(tenant_id=tenant_id)
            session.add(row)
        row.primary_color_light = primary_color_light
        row.primary_color_dark = primary_color_dark
        row.updated_by_user_id = actor_user_id
        await session.commit()
    invalidate_cache(tenant_id)


async def put_asset(
    tenant_id: str, kind: str, data: bytes, *, actor_user_id: Optional[str]
) -> AssetMeta:
    """Validate and store (insert or replace) an image. Raises ``BrandingImageError``."""
    mime = validate_image(kind, data)
    sha = hashlib.sha256(data).hexdigest()
    async with get_async_session_maker()() as session:
        row = await session.get(BrandingAsset, (tenant_id, kind))
        if row is None:
            row = BrandingAsset(tenant_id=tenant_id, kind=kind)
            session.add(row)
        row.mime = mime
        row.data = data
        row.sha256 = sha
        row.size = len(data)
        row.updated_by_user_id = actor_user_id
        await session.commit()
    invalidate_cache(tenant_id)
    return AssetMeta(mime=mime, sha256=sha, size=len(data), updated_at=None)


async def delete_asset(tenant_id: str, kind: str) -> bool:
    """Remove an image; returns False when there was nothing to remove."""
    async with get_async_session_maker()() as session:
        res = await session.execute(
            delete(BrandingAsset).where(
                BrandingAsset.tenant_id == tenant_id, BrandingAsset.kind == kind
            )
        )
        await session.commit()
    invalidate_cache(tenant_id)
    return bool(res.rowcount)


async def get_asset(tenant_id: str, kind: str) -> Optional[BrandingAsset]:
    """Load an image row with its bytes (``None`` if absent)."""
    if kind not in ASSET_KINDS:
        return None
    async with get_async_session_maker()() as session:
        row = await session.get(BrandingAsset, (tenant_id, kind))
        if row is not None:
            session.expunge(row)
        return row


@dataclass(frozen=True)
class EmailBranding:
    """What the email renderer needs: inline logo (if raster) and button colour."""

    logo_bytes: Optional[bytes]
    logo_mime: Optional[str]
    button_color: Optional[str]
    custom_logo: bool


async def get_email_branding(tenant_id: str) -> EmailBranding:
    """Branding for platform emails.

    The light-theme logo is used because emails render on a white card; the
    dark one is a fallback. SVG is skipped (most clients block it), but
    ``custom_logo`` stays true so the "Powered by AION" footer still appears.
    """
    snap = await get_branding(tenant_id)
    logo_bytes: Optional[bytes] = None
    logo_mime: Optional[str] = None
    for kind in ("logo_light", "logo_dark"):
        meta = snap.assets.get(kind)
        if meta and meta.mime in EMAIL_SAFE_MIMES:
            row = await get_asset(tenant_id, kind)
            if row is not None:
                logo_bytes, logo_mime = row.data, row.mime
            break
    return EmailBranding(
        logo_bytes=logo_bytes,
        logo_mime=logo_mime,
        button_color=snap.primary_color_light,
        custom_logo=snap.custom_logo,
    )
