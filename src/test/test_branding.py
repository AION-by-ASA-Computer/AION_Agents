"""Tenant branding: /admin/branding, public /branding, image validation and emails.

Covers the rules in docs/configuration/branding.md: strict hex colours,
magic-byte image detection with size limits, unsafe SVG rejection, the
public asset headers, audit entries without image bytes, and the email
logo / "Powered by AION" footer.
"""

from __future__ import annotations

import io
import json
from typing import List

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from src.test.smtp_fakes import configure_smtp, install_fake_smtp

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
ICO = b"\x00\x00\x01\x00" + b"\x00" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#123456"/></svg>'


async def _setup(monkeypatch, tmp_path):
    """Fresh SQLite DB with the default tenant and an admin user ``a1``."""
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    monkeypatch.delenv("AION_DEFAULT_TENANT_ID", raising=False)

    from src.branding import service
    from src.data import engine as engine_mod
    from src.data.bootstrap import ensure_bootstrap_schema
    from src.data.engine import get_async_session_maker, init_engine
    from src.data.models import User
    from src.notifications import smtp_settings

    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)
    service._CACHE.clear()
    smtp_settings._CACHE.clear()
    eng = init_engine()
    await ensure_bootstrap_schema(eng)
    async with get_async_session_maker()() as session:
        session.add(
            User(
                id="a1",
                tenant_id="default",
                identifier="root",
                email="root@x.io",
                roles_json='["admin"]',
            )
        )
        await session.commit()
    return get_async_session_maker()


def _auth():
    from src.api.auth_login import ChatAuthIdentity

    return ChatAuthIdentity(
        user_row_id="a1", identifier="root", via="chat_token", roles=["admin"]
    )


def _upload(data: bytes, content_type: str = "image/png") -> UploadFile:
    # The declared content type is deliberately irrelevant: detection uses magic bytes.
    return UploadFile(
        file=io.BytesIO(data),
        filename="logo",
        headers=Headers({"content-type": content_type}),
    )


async def _audit_ops(sm) -> List[tuple]:
    from sqlalchemy import select

    from src.data.models import AuditLog

    async with sm() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return [(r.operation, r.payload) for r in rows]


class _Req:
    """Minimal stand-in for starlette Request (only headers are read)."""

    def __init__(self, headers=None):
        self.headers = headers or {}


# --- image validation ---------------------------------------------------------


def test_sniff_and_validate_images():
    """Type comes from magic bytes; each kind accepts only its formats."""
    from src.branding.images import BrandingImageError, validate_image

    assert validate_image("logo_light", PNG) == "image/png"
    assert validate_image("logo_dark", JPEG) == "image/jpeg"
    assert validate_image("logo_light", SVG) == "image/svg+xml"
    assert validate_image("favicon", ICO) == "image/x-icon"

    cases = [
        (
            "logo_light",
            b"<html><script>alert(1)</script></html>",
            "unsupported_type",
            415,
        ),
        ("logo_light", ICO, "unsupported_type", 415),  # ico is favicon-only
        ("favicon", JPEG, "unsupported_type", 415),
        ("logo_light", b"", "empty_file", 422),
        ("favicon", b"\x89PNG\r\n\x1a\n" + b"\x00" * (256 * 1024), "too_large", 413),
        ("banner", PNG, "invalid_kind", 404),
    ]
    for kind, data, code, status in cases:
        with pytest.raises(BrandingImageError) as e:
            validate_image(kind, data)
        assert (e.value.code, e.value.status) == (code, status), kind


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
        b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>',
    ],
)
def test_unsafe_svg_rejected(payload):
    """SVGs with active content are refused even though they are served sandboxed."""
    from src.branding.images import BrandingImageError, validate_image

    with pytest.raises(BrandingImageError) as e:
        validate_image("logo_light", payload)
    assert e.value.code == "unsafe_svg"


# --- admin API ---------------------------------------------------------------


@pytest.mark.anyio
async def test_colors_validation_and_audit(monkeypatch, tmp_path):
    """Only #rrggbb is accepted; values are normalised and audited; null restores default."""
    sm = await _setup(monkeypatch, tmp_path)
    from src.api.admin_branding import (
        BrandingColorsBody,
        get_admin_branding,
        put_admin_branding,
    )

    state = await get_admin_branding(auth=_auth())
    assert state["primary_color_light"] is None and state["custom_logo"] is False

    for bad in ("red", "#12345", "#1234567", "#12345g", "#123456;}body{x"):
        with pytest.raises(HTTPException) as e:
            await put_admin_branding(
                BrandingColorsBody(primary_color_light=bad), auth=_auth()
            )
        assert e.value.status_code == 400
        assert e.value.detail["code"] == "invalid_color"

    res = await put_admin_branding(
        BrandingColorsBody(
            primary_color_light="#0A66C2", primary_color_dark=" #3b82f6 "
        ),
        auth=_auth(),
    )
    assert (res["primary_color_light"], res["primary_color_dark"]) == (
        "#0a66c2",
        "#3b82f6",
    )

    res = await put_admin_branding(
        BrandingColorsBody(primary_color_light="#0a66c2"), auth=_auth()
    )
    assert res["primary_color_dark"] is None

    ops = await _audit_ops(sm)
    assert [op for op, _ in ops] == ["branding.update", "branding.update"]


@pytest.mark.anyio
async def test_upload_replace_delete_logo(monkeypatch, tmp_path):
    """Upload sets custom_logo and a versioned URL; audit has metadata only; delete reverts."""
    sm = await _setup(monkeypatch, tmp_path)
    from src.api.admin_branding import (
        delete_admin_branding_asset,
        put_admin_branding_asset,
    )
    from src.api.branding import get_public_branding

    res = await put_admin_branding_asset(
        "logo_light", _upload(PNG, "text/html"), auth=_auth()
    )
    assert res["custom_logo"] is True
    assert res["logo_light"]["mime"] == "image/png"
    url1 = res["logo_light"]["url"]
    assert url1.startswith("/branding/assets/logo_light?v=")
    assert res["email_logo"] is True

    res = await put_admin_branding_asset("logo_light", _upload(SVG), auth=_auth())
    assert res["logo_light"]["url"] != url1  # new content -> new cache-busting URL
    assert res["email_logo"] is False  # SVG can't go in emails

    with pytest.raises(HTTPException) as e:
        await put_admin_branding_asset(
            "logo_light", _upload(b"GIF89a" + b"\0" * 10), auth=_auth()
        )
    assert e.value.status_code == 415

    class _Resp:
        headers: dict = {}

    pub = await get_public_branding(_Resp())
    assert pub["custom_logo"] is True and pub["logo_dark"] is None

    res = await delete_admin_branding_asset("logo_light", auth=_auth())
    assert res["custom_logo"] is False and res["logo_light"] is None

    ops = await _audit_ops(sm)
    assert [op for op, _ in ops] == [
        "branding.asset.put",
        "branding.asset.put",
        "branding.asset.delete",
    ]
    # Image bytes never end up in the audit log: metadata keys only.
    assert set(json.loads(ops[0][1])) == {"kind", "mime", "size", "sha256"}


@pytest.mark.anyio
async def test_public_asset_headers(monkeypatch, tmp_path):
    """Assets: nosniff always, CSP sandbox for SVG, immutable only for a matching ?v=, 304 on ETag."""
    await _setup(monkeypatch, tmp_path)
    from src.api.admin_branding import put_admin_branding_asset
    from src.api.branding import get_branding_asset

    res = await put_admin_branding_asset("logo_dark", _upload(SVG), auth=_auth())
    v = res["logo_dark"]["url"].split("v=")[1]

    r = await get_branding_asset("logo_dark", _Req(), v=v)
    assert r.status_code == 200 and r.body == SVG
    assert r.media_type == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "immutable" in r.headers["cache-control"]

    stale = await get_branding_asset("logo_dark", _Req(), v="deadbeef")
    assert "immutable" not in stale.headers["cache-control"]

    etag = r.headers["etag"]
    nm = await get_branding_asset("logo_dark", _Req({"if-none-match": etag}))
    assert nm.status_code == 304

    for kind in ("logo_light", "nope"):
        with pytest.raises(HTTPException) as e:
            await get_branding_asset(kind, _Req())
        assert e.value.status_code == 404


def test_admin_endpoints_require_auth(monkeypatch):
    """/admin/branding is admin-only; /branding is public."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.admin_branding import router as admin_router
    from src.api.branding import router as public_router

    monkeypatch.setenv("AION_ADMIN_PASSWORD_AUTH", "1")
    monkeypatch.setenv("AION_CHAT_PASSWORD_AUTH", "1")
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(public_router)
    client = TestClient(app)
    assert client.get("/admin/branding").status_code in (401, 403)
    assert client.put("/admin/branding", json={}).status_code in (401, 403)
    assert client.delete("/admin/branding/assets/logo_light").status_code in (401, 403)


# --- emails ------------------------------------------------------------------


@pytest.mark.anyio
async def test_email_with_raster_logo_and_color(monkeypatch, tmp_path):
    """Raster logo -> inline cid image + Powered by footer; button uses the light colour."""
    await _setup(monkeypatch, tmp_path)
    fake = install_fake_smtp(monkeypatch)
    from src.api.admin_branding import (
        BrandingColorsBody,
        put_admin_branding,
        put_admin_branding_asset,
    )
    from src.api.admin_smtp import SmtpTestBody, test_smtp
    from src.branding.service import get_email_branding
    from src.notifications import email_templates

    await configure_smtp()
    await put_admin_branding_asset("logo_light", _upload(PNG), auth=_auth())
    await put_admin_branding(
        BrandingColorsBody(primary_color_light="#facc15"), auth=_auth()
    )

    branding = await get_email_branding("default")
    _, text, html = email_templates.render(
        "invite",
        "en",
        {
            "name": "N",
            "identifier": "n",
            "link": "https://x/l",
            "expires": "x",
            "platform": "Acme",
        },
        branding=branding,
    )
    assert 'src="cid:aion-brand-logo"' in html
    assert "https://aion-asa.com" in html and "Powered by" in html
    assert "background:#facc15;color:#18181b;" in html  # light bg -> dark text
    assert text.rstrip().endswith("https://aion-asa.com")

    res = await test_smtp(SmtpTestBody(), auth=_auth())
    assert res["ok"] is True
    msg = fake.sent[0]
    images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
    assert len(images) == 1
    assert images[0]["Content-ID"] == "<aion-brand-logo>"
    assert images[0].get_payload(decode=True) == PNG


@pytest.mark.anyio
async def test_email_svg_logo_and_default(monkeypatch, tmp_path):
    """SVG logo -> no inline image but still Powered by; no branding -> unchanged default look."""
    await _setup(monkeypatch, tmp_path)
    from src.api.admin_branding import put_admin_branding_asset
    from src.branding.service import get_email_branding
    from src.notifications import email_templates

    values = {
        "name": "N",
        "identifier": "n",
        "link": "l",
        "expires": "x",
        "platform": "AION",
    }
    _, text, html = email_templates.render(
        "invite", "it", values, branding=await get_email_branding("default")
    )
    assert "cid:" not in html and "Powered by" not in html
    assert "background:#18181b;color:#ffffff;" in html
    assert "aion-asa.com" not in text

    await put_admin_branding_asset("logo_light", _upload(SVG), auth=_auth())
    _, _, html = email_templates.render(
        "invite", "it", values, branding=await get_email_branding("default")
    )
    assert "cid:" not in html and "Powered by" in html
