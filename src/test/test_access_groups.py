"""Phase 1 of the access-control plan: user groups and access rules.

Covers the rule semantics ("no rule = no limit" per resource type, union of
the subjects that have a rule, ``*`` and "nothing" rules, default-profile
fallback order), the legacy ``user_profile_access`` compatibility, the
``/admin/groups`` + ``/admin/users/{id}/...`` endpoints, principal cache
invalidation on writes, user deletion cleanup and the Alembic data step.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi import HTTPException

from src.access import store
from src.access.policy import ProfileAccessDenied, resolve_profile_for
from src.access.principal import load_principal

ADMIN = "row-admin"
ANNA = "row-anna"
BRUNO = "row-bruno"
P1, P2, P3 = "coding_workspace", "infra_sre", "data_agent"


async def _setup(monkeypatch, tmp_path):
    """Isolated SQLite DB with an admin and two plain users, no rules."""
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    monkeypatch.setenv("AION_DEFAULT_TENANT_ID", "default")
    from src.data import engine as engine_mod
    from src.data.bootstrap import ensure_bootstrap_schema
    from src.data.engine import get_async_session_maker, init_engine
    from src.data.models import User

    monkeypatch.setattr(engine_mod, "_engine", None)
    monkeypatch.setattr(engine_mod, "_session_factory", None)
    await ensure_bootstrap_schema(init_engine())
    async with get_async_session_maker()() as session:
        for uid, ident, roles in (
            (ADMIN, "boss", ["admin"]),
            (ANNA, "anna", []),
            (BRUNO, "bruno", []),
        ):
            session.add(
                User(
                    id=uid,
                    tenant_id="default",
                    identifier=ident,
                    roles_json=json.dumps(roles),
                )
            )
        await session.commit()
    return get_async_session_maker()


def _admin():
    from src.api.auth_login import ChatAuthIdentity

    return ChatAuthIdentity(
        user_row_id=ADMIN, identifier="boss", via="chat_token", roles=["admin"]
    )


async def _group(slug: str, members=(), profiles=None):
    g = await store.create_group(slug=slug, name=slug.title())
    if members:
        await store.set_members(g.id, list(members))
    if profiles is not None:
        await store.set_rules(store.SUBJECT_GROUP, g.id, {"profile": profiles})
    return g


# --- rule semantics ----------------------------------------------------------


@pytest.mark.anyio
async def test_no_groups_no_rules_means_no_limit(monkeypatch, tmp_path):
    """A new user (no group, no rule) can use every profile; a group without
    rules changes nothing."""
    await _setup(monkeypatch, tmp_path)
    assert (await load_principal(ANNA)).profile_grants is None
    await _group("empty", members=[ANNA])
    p = await load_principal(ANNA)
    assert p.profile_grants is None and p.groups == ("empty",)


@pytest.mark.anyio
async def test_group_rule_limits_members_only(monkeypatch, tmp_path):
    """A group's profile rule applies to its members and nobody else."""
    await _setup(monkeypatch, tmp_path)
    await _group("sales", members=[ANNA], profiles=[P1])
    assert (await load_principal(ANNA)).profile_grants == (P1,)
    assert (await load_principal(BRUNO)).profile_grants is None


@pytest.mark.anyio
async def test_union_of_groups_with_a_rule(monkeypatch, tmp_path):
    """Union across groups that have a rule; groups without one do not count;
    a ``*`` rule lifts the limit."""
    await _setup(monkeypatch, tmp_path)
    await _group("a", members=[ANNA], profiles=[P1])
    await _group("b", members=[ANNA], profiles=[P2])
    await _group("c", members=[ANNA])  # no profile rule
    assert set((await load_principal(ANNA)).profile_grants) == {P1, P2}
    await _group("all", members=[ANNA], profiles=["*"])
    assert (await load_principal(ANNA)).profile_grants is None


@pytest.mark.anyio
async def test_nothing_rule_blocks_every_profile(monkeypatch, tmp_path):
    """``[]`` is a rule granting no profile: even the default one is denied."""
    await _setup(monkeypatch, tmp_path)
    await _group("none", members=[ANNA], profiles=[])
    p = await load_principal(ANNA)
    assert p.profile_grants == ()
    with pytest.raises(ProfileAccessDenied):
        resolve_profile_for(p, "aion_std")
    rules = await store.get_rules(
        store.SUBJECT_GROUP, (await store.list_groups())[0].id
    )
    assert rules == {"profile": []}


@pytest.mark.anyio
async def test_fallback_prefers_direct_rule_then_groups(monkeypatch, tmp_path):
    """The default profile falls back to the user's direct rule first."""
    await _setup(monkeypatch, tmp_path)
    await _group("g", members=[ANNA], profiles=[P1])
    await store.set_rules(store.SUBJECT_USER, ANNA, {"profile": [P2]})
    p = await load_principal(ANNA)
    assert p.profile_grants == (P2, P1)
    assert resolve_profile_for(p, "aion_std") == P2


@pytest.mark.anyio
async def test_legacy_rows_still_count_and_are_replaced_on_write(monkeypatch, tmp_path):
    """``user_profile_access`` rows act as a direct rule until rewritten."""
    sm = await _setup(monkeypatch, tmp_path)
    from src.data.models import UserProfileAccess

    async with sm() as session:
        session.add(
            UserProfileAccess(user_id=ANNA, tenant_id="default", profile_slug=P3)
        )
        await session.commit()
    assert (await load_principal(ANNA)).profile_grants == (P3,)
    await store.set_rules(store.SUBJECT_USER, ANNA, {"profile": None})
    assert (await load_principal(ANNA)).profile_grants is None
    async with sm() as session:
        assert await session.get(UserProfileAccess, 1) is None


# --- cache invalidation ------------------------------------------------------


@pytest.mark.anyio
async def test_writes_invalidate_member_principals(monkeypatch, tmp_path):
    """Rule, membership and group deletion changes apply at the next request
    even with a long cache TTL."""
    monkeypatch.setenv("AION_ACCESS_CACHE_TTL_SEC", "300")
    await _setup(monkeypatch, tmp_path)
    g = await _group("ops", members=[ANNA])
    assert (await load_principal(ANNA)).profile_grants is None  # cached
    await store.set_rules(store.SUBJECT_GROUP, g.id, {"profile": [P1]})
    assert (await load_principal(ANNA)).profile_grants == (P1,)
    await store.set_members(g.id, [BRUNO])
    assert (await load_principal(ANNA)).profile_grants is None
    assert (await load_principal(BRUNO)).profile_grants == (P1,)
    await store.delete_group(g.id)
    assert (await load_principal(BRUNO)).profile_grants is None


# --- admin API ---------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_api_group_lifecycle_and_effective_access(monkeypatch, tmp_path):
    """Create, populate, restrict, inspect and delete a group via the API."""
    await _setup(monkeypatch, tmp_path)
    from src.api import admin_groups as api

    auth = _admin()
    g = await api.create_group(api.GroupCreate(slug="sales", name="Sales"), auth=auth)
    with pytest.raises(HTTPException) as exc:
        await api.create_group(api.GroupCreate(slug="sales", name="X"), auth=auth)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await api.create_group(api.GroupCreate(slug="Bad Slug!", name="X"), auth=auth)
    assert exc.value.status_code == 422

    await api.set_group_members(g["id"], api.MembersUpdate(user_ids=[ANNA]), auth=auth)
    with pytest.raises(HTTPException) as exc:
        await api.set_group_members(
            g["id"], api.MembersUpdate(user_ids=["ghost"]), auth=auth
        )
    assert exc.value.status_code == 422

    await api.set_group_rules(
        g["id"], api.RulesUpdate(rules={"profile": [P1]}), auth=auth
    )
    for bad in ({"profile": ["no_such_profile"]}, {"capability": ["x"]}):
        with pytest.raises(HTTPException) as exc:
            await api.set_group_rules(g["id"], api.RulesUpdate(rules=bad), auth=auth)
        assert exc.value.status_code == 422

    detail = await api.get_group(g["id"])
    assert detail["member_count"] == 1 and detail["members"][0]["identifier"] == "anna"
    assert detail["rules"] == {"profile": [P1]}

    eff = await api.get_effective_access(ANNA)
    prof = eff["access"]["profile"]
    assert prof["limited"] and prof["allowed"] == [P1]
    assert [s["kind"] for s in prof["sources"]] == ["user", "group"]
    assert prof["sources"][0]["rule"] is None

    eff_bruno = await api.get_effective_access(BRUNO)
    assert eff_bruno["access"]["profile"] == {
        "limited": False,
        "allowed": None,
        "sources": [{"kind": "user", "id": BRUNO, "name": None, "rule": None}],
    }

    await api.delete_group(g["id"], auth=auth)
    assert (await load_principal(ANNA)).profile_grants is None
    with pytest.raises(HTTPException) as exc:
        await api.get_group(g["id"])
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_admin_api_user_groups_and_rules(monkeypatch, tmp_path):
    """User-side endpoints: replace groups, direct rules, audit entries."""
    sm = await _setup(monkeypatch, tmp_path)
    from sqlalchemy import select

    from src.api import admin_groups as api
    from src.data.models import AuditLog

    auth = _admin()
    g1 = await _group("one", profiles=[P1])
    g2 = await _group("two", profiles=[P2])
    await api.set_user_groups(
        ANNA, api.UserGroupsUpdate(group_ids=[g1.id, g2.id]), auth=auth
    )
    assert [g["slug"] for g in (await api.get_user_groups(ANNA))["items"]] == [
        "one",
        "two",
    ]
    with pytest.raises(HTTPException) as exc:
        await api.set_user_groups(
            ANNA, api.UserGroupsUpdate(group_ids=["nope"]), auth=auth
        )
    assert exc.value.status_code == 422

    await api.set_user_rules(ANNA, api.RulesUpdate(rules={"profile": [P3]}), auth=auth)
    assert (await api.get_user_rules(ANNA))["rules"] == {"profile": [P3]}
    assert set((await load_principal(ANNA)).profile_grants) == {P1, P2, P3}

    with pytest.raises(HTTPException) as exc:
        await api.get_user_rules("ghost")
    assert exc.value.status_code == 404

    async with sm() as session:
        ops = set((await session.execute(select(AuditLog.operation))).scalars())
    assert {"user.groups.update", "user.rules.update"} <= ops


@pytest.mark.anyio
async def test_legacy_profiles_endpoint_is_a_shim(monkeypatch, tmp_path):
    """``/admin/users/{id}/profiles`` reads/writes the direct profile rule;
    an empty list removes the rule (legacy "all profiles")."""
    await _setup(monkeypatch, tmp_path)
    from src.api.admin import (
        UserProfilesUpdate,
        get_user_profiles,
        update_user_profiles,
    )

    await update_user_profiles(
        ANNA, UserProfilesUpdate(allowed_profiles=[P1]), auth=_admin()
    )
    assert (await get_user_profiles(ANNA)) == {"allowed_profiles": [P1]}
    assert (await load_principal(ANNA)).profile_grants == (P1,)
    await update_user_profiles(
        ANNA, UserProfilesUpdate(allowed_profiles=[]), auth=_admin()
    )
    assert (await load_principal(ANNA)).profile_grants is None


@pytest.mark.anyio
async def test_delete_user_access_cleans_memberships(monkeypatch, tmp_path):
    """Deleting a user removes memberships and direct rules."""
    await _setup(monkeypatch, tmp_path)
    g = await _group("g", members=[ANNA, BRUNO])
    await store.set_rules(store.SUBJECT_USER, ANNA, {"profile": [P1]})
    await store.delete_user_access(ANNA)
    assert await store.list_member_ids(g.id) == [BRUNO]
    assert await store.get_rules(store.SUBJECT_USER, ANNA) == {}


# --- Alembic data step -------------------------------------------------------


@pytest.mark.anyio
async def test_migration_copies_legacy_profile_access(monkeypatch, tmp_path):
    """j8g9m0e038 copies ``user_profile_access`` rows into ``access_grants``
    as direct user rules, in order, and is idempotent."""
    sm = await _setup(monkeypatch, tmp_path)
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, select

    from src.data.models import AccessGrant, UserProfileAccess

    async with sm() as session:
        for slug in (P2, P1):
            session.add(
                UserProfileAccess(user_id=ANNA, tenant_id="default", profile_slug=slug)
            )
        await session.commit()

    mod = importlib.import_module(
        "migrations.versions.j8g9m0e038_user_groups_access_grants"
    )
    sync_engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    try:
        for _ in range(2):  # second run must not duplicate rows
            with sync_engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    mod.upgrade()
    finally:
        sync_engine.dispose()

    async with sm() as session:
        rows = (
            (await session.execute(select(AccessGrant).order_by(AccessGrant.id)))
            .scalars()
            .all()
        )
    assert [
        (r.subject_type, r.subject_id, r.resource_type, r.resource_id) for r in rows
    ] == [
        ("user", ANNA, "profile", P2),
        ("user", ANNA, "profile", P1),
    ]
    assert (await load_principal(ANNA)).profile_grants == (P2, P1)


@pytest.mark.anyio
async def test_principal_falls_back_when_group_tables_are_missing(
    monkeypatch, tmp_path
):
    """A database not migrated yet (no group tables) must not break every
    request: the legacy per-user allowlist still applies."""
    sm = await _setup(monkeypatch, tmp_path)
    from sqlalchemy import text

    from src.data.models import UserProfileAccess

    async with sm() as session:
        session.add(
            UserProfileAccess(user_id=ANNA, tenant_id="default", profile_slug=P1)
        )
        for table in ("access_grants", "user_group_members", "user_groups"):
            await session.execute(text(f"DROP TABLE {table}"))
        await session.commit()
    p = await load_principal(ANNA)
    assert p.profile_grants == (P1,) and p.groups == ()
