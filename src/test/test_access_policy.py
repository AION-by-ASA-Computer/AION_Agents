"""Phase 0 of the access-control plan (``src.access``).

Covers: the single profile-access decision point (legacy semantics kept),
roles read from the DB instead of the login token, principal cache
invalidation, the generic ``can`` / ``require_permission`` checks and the
per-user MCP server filter applied at agent build (resident + deferred).
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.access.policy import (
    ProfileAccessDenied,
    can,
    effective_permissions,
    filter_profiles,
    resolve_profile_for,
)
from src.access.principal import (
    Principal,
    invalidate_principal,
    load_principal,
    load_principal_for,
)

ADMIN = "row-admin"
RESTRICTED = "row-restricted"
FREE = "row-free"


async def _setup(monkeypatch, tmp_path):
    """Isolated SQLite DB with an admin, a restricted and an unrestricted user."""
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    from src.data import engine as engine_mod
    from src.data.bootstrap import ensure_bootstrap_schema
    from src.data.engine import get_async_session_maker, init_engine
    from src.data.models import User, UserProfileAccess

    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)
    await ensure_bootstrap_schema(init_engine())
    async with get_async_session_maker()() as session:
        for uid, ident, roles in (
            (ADMIN, "boss", ["admin"]),
            (RESTRICTED, "Mario_Rossi", []),
            (FREE, "free", []),
        ):
            session.add(
                User(
                    id=uid,
                    tenant_id="default",
                    identifier=ident,
                    roles_json=json.dumps(roles),
                )
            )
        await session.flush()
        for slug in ("coding_workspace", "infra_sre"):
            session.add(
                UserProfileAccess(
                    user_id=RESTRICTED, tenant_id="default", profile_slug=slug
                )
            )
        await session.commit()
    return get_async_session_maker()


# --- pure policy (no DB) -----------------------------------------------------


def test_unrestricted_principal_keeps_requested_profile():
    """No grants = every profile, the requested name is returned unchanged."""
    p = Principal(user_row_id="u", exists=True)
    assert resolve_profile_for(p, "infra_sre") == "infra_sre"
    profiles = [{"slug": "a"}, {"slug": "b"}]
    assert filter_profiles(p, profiles) == profiles


def test_restricted_principal_fallback_and_denial():
    """Default profile falls back to the first grant; others are denied."""
    p = Principal(user_row_id="u", exists=True, profile_grants=("coding_workspace",))
    assert resolve_profile_for(p, "coding_workspace") == "coding_workspace"
    assert resolve_profile_for(p, "aion_std") == "coding_workspace"
    with pytest.raises(ProfileAccessDenied) as exc:
        resolve_profile_for(p, "infra_sre")
    assert exc.value.allowed == ["coding_workspace"]
    assert filter_profiles(
        p, [{"slug": "infra_sre"}, {"slug": "coding_workspace"}]
    ) == [{"slug": "coding_workspace"}]


def test_can_admin_section_and_unknown_resource():
    """Admin areas (Phase 5): the super-admin holds them all, a user without
    rules none; unknown types are denied."""
    admin = Principal(user_row_id="a", roles=("admin",))
    user = Principal(user_row_id="u")
    assert can(admin, "write", "admin_section", "agents")
    assert not can(user, "read", "admin_section", "agents")
    reader = Principal(user_row_id="r", allowed={"admin_section": ("agents:read",)})
    assert can(reader, "read", "admin_section", "agents")
    assert not can(reader, "write", "admin_section", "agents")
    assert can(user, "use", "skill", "anything")  # not restricted in Phase 0
    assert not can(admin, "use", "typo_resource", "x")


def test_admin_is_restricted_like_everyone_on_profiles():
    """Decision D1: the admin role does not bypass profile restrictions."""
    p = Principal(user_row_id="a", roles=("admin",), profile_grants=("x",))
    assert not can(p, "use", "profile", "infra_sre")


def test_effective_permissions_shape():
    """``/auth/me`` permission strings for restricted users and admins."""
    p = Principal(user_row_id="u", profile_grants=("a", "b"))
    perms = effective_permissions(p)
    assert "profile:a:use" in perms and "profile:*:use" not in perms
    assert not any(x.startswith("admin_section") for x in perms)
    admin = effective_permissions(Principal(user_row_id="x", roles=("admin",)))
    assert "admin_section:agents:write" in admin and "profile:*:use" in admin
    assert "admin_section:observability:read" in admin


# --- DB-backed principal -----------------------------------------------------


@pytest.mark.anyio
async def test_principal_loads_grants_roles_and_identifier(monkeypatch, tmp_path):
    """Grants keep insertion order (fallback stability); identifier lookup works."""
    await _setup(monkeypatch, tmp_path)
    p = await load_principal(RESTRICTED)
    assert p.exists and p.profile_grants == ("coding_workspace", "infra_sre")
    assert (await load_principal(ADMIN)).is_superadmin
    assert (await load_principal_for("mario_rossi")).user_row_id == RESTRICTED
    assert (await load_principal(FREE)).profile_grants is None
    missing = await load_principal("ghost")
    assert not missing.exists and missing.profile_grants is None


@pytest.mark.anyio
async def test_principal_cache_and_invalidation(monkeypatch, tmp_path):
    """Writes are invisible until ``invalidate_principal`` (or the TTL)."""
    monkeypatch.setenv("AION_ACCESS_CACHE_TTL_SEC", "300")
    sm = await _setup(monkeypatch, tmp_path)
    from src.data.models import User

    assert not (await load_principal(FREE)).is_superadmin
    async with sm() as session:
        (await session.get(User, FREE)).roles_json = '["admin"]'
        await session.commit()
    assert not (await load_principal(FREE)).is_superadmin  # cached
    invalidate_principal(FREE)
    assert (await load_principal(FREE)).is_superadmin


@pytest.mark.anyio
async def test_chat_token_roles_come_from_db(monkeypatch, tmp_path):
    """A demoted admin loses admin access without re-login; a missing user
    row keeps the token roles (legacy behaviour)."""
    sm = await _setup(monkeypatch, tmp_path)
    from src.api.auth_login import _current_roles
    from src.data.models import User

    assert await _current_roles(FREE, ["admin"]) == []
    assert await _current_roles(ADMIN, []) == ["admin"]
    assert await _current_roles("ghost", ["admin"]) == ["admin"]

    async with sm() as session:
        (await session.get(User, ADMIN)).roles_json = "[]"
        await session.commit()
    invalidate_principal(ADMIN)
    assert await _current_roles(ADMIN, ["admin"]) == []


@pytest.mark.anyio
async def test_require_permission_ignores_owner_roles_for_api_keys(
    monkeypatch, tmp_path
):
    """An API key of an admin carries no roles: admin sections stay closed."""
    await _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("AION_ADMIN_PASSWORD_AUTH", "1")
    from src.api.auth_login import (
        ChatAuthIdentity,
        require_admin_role,
        require_permission,
    )

    key_ctx = ChatAuthIdentity(user_row_id=ADMIN, via="api_key", roles=[])
    with pytest.raises(HTTPException) as exc:
        await require_admin_role(key_ctx)
    assert exc.value.status_code == 403

    admin_ctx = ChatAuthIdentity(user_row_id=ADMIN, via="chat_token", roles=["admin"])
    assert await require_admin_role(admin_ctx) is admin_ctx

    dep = require_permission("profile", "use", "infra_sre")
    user_ctx = ChatAuthIdentity(user_row_id=RESTRICTED, via="chat_token")
    assert await dep(user_ctx) is user_ctx
    with pytest.raises(HTTPException) as exc:
        await require_permission("profile", "use", "aion_tutor")(user_ctx)
    assert exc.value.detail["code"] == "permission_denied"


@pytest.mark.anyio
async def test_admin_profiles_endpoint_invalidates_principal(monkeypatch, tmp_path):
    """POST /admin/users/{id}/profiles applies at the next request."""
    monkeypatch.setenv("AION_ACCESS_CACHE_TTL_SEC", "300")
    await _setup(monkeypatch, tmp_path)
    from src.api.admin import UserProfilesUpdate, update_user_profiles
    from src.api.auth_login import ChatAuthIdentity

    assert (await load_principal(FREE)).profile_grants is None
    await update_user_profiles(
        FREE,
        UserProfilesUpdate(allowed_profiles=["coding_workspace"]),
        auth=ChatAuthIdentity(user_row_id=ADMIN, identifier="boss", roles=["admin"]),
    )
    assert (await load_principal(FREE)).profile_grants == ("coding_workspace",)


# --- per-user MCP filter -----------------------------------------------------


@pytest.mark.anyio
async def test_mcp_filter_drops_servers_disabled_by_user(monkeypatch, tmp_path):
    """Disabled + ``user_may_disable`` -> removed (also from deferred groups);
    ``user_may_disable=False`` -> kept; the shared profile is never mutated."""
    sm = await _setup(monkeypatch, tmp_path)
    from src.access.mcp_filter import effective_profile_for_user
    from src.data.models import McpServerConfig, UserMcpPreference

    async with sm() as session:
        for slug, may_disable in (("grafana", True), ("prometheus", False)):
            session.add(
                McpServerConfig(
                    id=f"cfg-{slug}",
                    server_slug=slug,
                    display_name=slug,
                    user_may_disable=may_disable,
                )
            )
            session.add(
                UserMcpPreference(
                    id=f"pref-{slug}",
                    user_id="free",
                    tenant_id="default",
                    server_slug=slug,
                    is_active=False,
                )
            )
        await session.commit()

    profile = SimpleNamespace(
        slug="p", mcp_servers=["grafana", "prometheus", "skills_hub"]
    )
    original = copy.deepcopy(profile.mcp_servers)
    eff = await effective_profile_for_user(profile, "free", "default")
    assert eff.mcp_servers == ["prometheus", "skills_hub"]
    assert profile.mcp_servers == original

    from src.runtime.tool_exposure import activatable_groups

    assert "grafana" not in activatable_groups(eff)
    # Another user without preferences gets the very same object back.
    assert await effective_profile_for_user(profile, "boss", "default") is profile
