"""Phase 2 of the access-control plan: rules on skills, MCP servers and native tools.

Covers:
- the policy helpers (``filter_allowed`` and glob rules, "no rule = no limit");
- rule validation in the admin API (catalogue check, glob patterns,
  ``unknown_resources``, types still not editable) and ``/admin/access/catalog``;
- the product rule: skill/MCP rules never restrict a profile the user may use,
  and the MCP servers of usable profiles stay configurable in Integrations;
- enforcement in ``/v1/integrations`` (list, activation, credentials) and the
  ``/v1/me/access`` view.

Reuses the isolated-DB fixture helpers of ``test_access_groups.py``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.access import store
from src.access.policy import can, filter_allowed, resolve_profile_for
from src.access.principal import load_principal
from test_access_groups import ANNA, BRUNO, _admin, _setup

# data_agent uses session_sandbox, skills_hub, query_memory (config/profiles).
PROFILE = "data_agent"
PROFILE_MCP = {"session_sandbox", "skills_hub", "query_memory"}


def _chat(row_id: str, ident: str):
    """Chat-token identity of a plain user."""
    from src.api.auth_login import ChatAuthIdentity

    return ChatAuthIdentity(
        user_row_id=row_id, identifier=ident, via="chat_token", roles=[]
    )


async def _add_mcp_configs(slugs):
    """Integration rows enabled for users (what ``/v1/integrations`` lists)."""
    from src.data.engine import get_async_session_maker
    from src.data.models import McpServerConfig

    async with get_async_session_maker()() as session:
        for slug in slugs:
            session.add(
                McpServerConfig(
                    id=f"cfg-{slug}",
                    server_slug=slug,
                    display_name=slug,
                    is_enabled_for_users=True,
                )
            )
        await session.commit()


# --- policy ------------------------------------------------------------------


@pytest.mark.anyio
async def test_skill_rules_union_and_globs(monkeypatch, tmp_path):
    """No rule = everything; group + direct rules add up; globs match names."""
    await _setup(monkeypatch, tmp_path)
    names = ["sql_select", "sql_join", "web_search", "pdf_tools"]

    assert filter_allowed(await load_principal(ANNA), "skill", names) == names

    g = await store.create_group(slug="analysts", name="Analysts")
    await store.set_members(g.id, [ANNA])
    await store.set_rules(store.SUBJECT_GROUP, g.id, {"skill": ["sql_*"]})
    await store.set_rules(store.SUBJECT_USER, ANNA, {"skill": ["pdf_tools"]})

    anna = await load_principal(ANNA)
    assert filter_allowed(anna, "skill", names) == [
        "sql_select",
        "sql_join",
        "pdf_tools",
    ]
    assert can(anna, "use", "skill", "sql_join")
    assert not can(anna, "use", "skill", "web_search")
    # Other types are still unrestricted, and so is Bruno.
    assert can(anna, "use", "mcp_server", "grafana")
    assert can(await load_principal(BRUNO), "use", "skill", "web_search")


@pytest.mark.anyio
async def test_skill_and_mcp_rules_never_restrict_usable_profiles(
    monkeypatch, tmp_path
):
    """Product rule: a profile the user may use works even with "nothing" rules
    on skills, MCP servers and native tools."""
    await _setup(monkeypatch, tmp_path)
    await store.set_rules(
        store.SUBJECT_USER,
        ANNA,
        {"profile": [PROFILE], "skill": [], "mcp_server": [], "native_tool_group": []},
    )
    anna = await load_principal(ANNA)
    assert resolve_profile_for(anna, PROFILE) == PROFILE
    assert not can(anna, "use", "mcp_server", "query_memory")

    from src.access.mcp_filter import configurable_mcp_servers

    # The profile's servers stay configurable although no MCP server is granted.
    assert await configurable_mcp_servers(anna) == PROFILE_MCP


# --- admin API -----------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_rules_validate_against_catalogue(monkeypatch, tmp_path):
    """Known ids, matching globs and ``*`` are accepted; the rest is a 422."""
    await _setup(monkeypatch, tmp_path)
    from src.access.catalog import resource_ids
    from src.api import admin_groups as api

    auth = _admin()
    g = await api.create_group(api.GroupCreate(slug="ops", name="Ops"), auth=auth)
    skill = resource_ids("skill")[0]
    native = resource_ids("native_tool_group")[0]
    ok = {
        "skill": [skill, "*_*"],
        "mcp_server": ["grafana"],
        "native_tool_group": [native],
    }
    res = await api.set_group_rules(g["id"], api.RulesUpdate(rules=ok), auth=auth)
    assert res["rules"]["mcp_server"] == ["grafana"]
    assert set(res["rules"]["skill"]) == {skill, "*_*"}

    for bad, code in (
        ({"skill": ["no_such_skill"]}, "unknown_resources"),
        ({"skill": ["zzz_nothing_*"]}, "unknown_resources"),
        ({"mcp_server": ["nope"]}, "unknown_resources"),
        ({"billing": ["x"]}, "resource_type_not_editable"),
        ({"profile": ["data_*"]}, "unknown_profiles"),
    ):
        with pytest.raises(HTTPException) as exc:
            await api.set_group_rules(g["id"], api.RulesUpdate(rules=bad), auth=auth)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == code

    # The member sees the restriction in the effective-access view.
    await api.set_group_members(g["id"], api.MembersUpdate(user_ids=[ANNA]), auth=auth)
    eff = (await api.get_effective_access(ANNA))["access"]
    assert eff["mcp_server"]["limited"] and eff["mcp_server"]["allowed"] == ["grafana"]
    assert eff["profile"]["limited"] is False


@pytest.mark.anyio
async def test_admin_access_catalog_lists_every_editable_type(monkeypatch, tmp_path):
    """``/admin/access/catalog`` feeds the rule editors of the admin-ui."""
    await _setup(monkeypatch, tmp_path)
    from src.api import admin_groups as api

    types = (await api.get_access_catalog())["types"]
    assert set(types) == set(store.EDITABLE_RESOURCE_TYPES)
    assert any(x["id"] == PROFILE for x in types["profile"])
    assert any(x["id"] == "grafana" for x in types["mcp_server"])
    assert all({"id", "name", "description"} <= set(x) for x in types["skill"])


# --- user endpoints --------------------------------------------------------------


@pytest.mark.anyio
async def test_integrations_follow_mcp_rules(monkeypatch, tmp_path):
    """List, activation and credentials honour the MCP rule plus the servers
    of usable profiles; switching off and unrestricted users are unaffected."""
    await _setup(monkeypatch, tmp_path)
    await _add_mcp_configs(["grafana", "odoo", "plane", "query_memory"])
    await store.set_rules(
        store.SUBJECT_USER, ANNA, {"profile": [PROFILE], "mcp_server": ["odoo"]}
    )
    from src.api.v1 import mcp_integrations as api

    api.clear_integrations_cache()
    anna, bruno = _chat(ANNA, "anna"), _chat(BRUNO, "bruno")

    listed = {
        i["server_slug"]
        for i in (await api.list_available_integrations(anna))["integrations"]
    }
    assert listed == {"odoo", "query_memory"}
    listed_b = {
        i["server_slug"]
        for i in (await api.list_available_integrations(bruno))["integrations"]
    }
    assert listed_b == {"grafana", "odoo", "plane", "query_memory"}

    with pytest.raises(HTTPException) as exc:
        await api.patch_integration_preference(
            "grafana", api.PreferenceBody(is_active=True), auth=anna
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "mcp_server_not_allowed"
    # Switching off is always possible, allowed servers can be switched on.
    await api.patch_integration_preference(
        "grafana", api.PreferenceBody(is_active=False), auth=anna
    )
    await api.patch_integration_preference(
        "odoo", api.PreferenceBody(is_active=True), auth=anna
    )

    monkeypatch.setenv("AION_MCP_USER_CREDENTIALS", "1")
    with pytest.raises(HTTPException) as exc:
        await api.save_credentials(
            api.CredentialSetBody(server_slug="plane", credentials={"K": "v"}),
            auth=anna,
        )
    assert exc.value.status_code == 403

    # A rule change shows up at once (the scope is part of the cache key).
    await store.set_rules(store.SUBJECT_USER, ANNA, {"mcp_server": None})
    listed = {
        i["server_slug"]
        for i in (await api.list_available_integrations(anna))["integrations"]
    }
    assert listed == {"grafana", "odoo", "plane", "query_memory"}


@pytest.mark.anyio
async def test_me_access_view(monkeypatch, tmp_path):
    """``/v1/me/access`` expands rules into existing ids for the client."""
    await _setup(monkeypatch, tmp_path)
    g = await store.create_group(slug="sre", name="SRE")
    await store.set_members(g.id, [ANNA])
    await store.set_rules(
        store.SUBJECT_GROUP, g.id, {"profile": [PROFILE], "mcp_server": ["grafana"]}
    )
    from src.api.v1.me_access import get_my_access

    mine = await get_my_access(_chat(ANNA, "anna"))
    assert mine["groups"] == ["sre"]
    acc = mine["access"]
    assert acc["profile"] == {"limited": True, "allowed": [PROFILE]}
    assert acc["mcp_server"]["allowed"] == ["grafana"]
    assert set(acc["mcp_server"]["configurable"]) == PROFILE_MCP | {"grafana"}
    assert acc["skill"] == {"limited": False, "allowed": None}

    other = (await get_my_access(_chat(BRUNO, "bruno")))["access"]
    assert other["mcp_server"] == {
        "limited": False,
        "allowed": None,
        "configurable": None,
    }


def test_skill_catalogue_hides_distiller_drafts(monkeypatch):
    """Draft skills (written by the skill distiller, not yet promoted) never
    appear in the rule editors or private-profile pickers: same list as the
    admin-ui Skills page (``status=verified``)."""
    from src.access.catalog import resource_ids
    from src.skill_registry import skill_registry

    metas = {
        "real_skill": {"status": "verified", "description": "kept"},
        "promoted_skill": {"status": "verified", "source": "generated"},
        "draft_skill": {"status": "draft", "source": "generated"},
    }
    monkeypatch.setattr(skill_registry, "reload_if_stale", lambda **_: None)
    monkeypatch.setattr(skill_registry, "get_all_names", lambda: list(metas))
    monkeypatch.setattr(skill_registry, "get_meta", lambda n: metas.get(n))
    assert sorted(resource_ids("skill")) == ["promoted_skill", "real_skill"]
