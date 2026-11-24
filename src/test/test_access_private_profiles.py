"""Phase 3 of the access-control plan: private profiles of end users.

Covers:
- slug generation (``usr_`` namespace, unique suffix) and ownership: only the
  owner may use a private profile, admins and other users may not;
- validation on save (items must exist and be allowed, count limit, the
  ``private_profiles.create`` capability);
- revocation at runtime: a revoked skill / MCP server disappears from the
  profile the agent gets, comes back when re-granted, and is reported as
  ``unavailable`` to the editor;
- the ``/v1/me/profiles`` API (CRUD, duplicate, building blocks), the
  ``GET /profiles`` listing, cron jobs paused on delete, cleanup on user
  deletion, the in-memory overlay reload and the idempotent migration.

Reuses the isolated-DB helpers of ``test_access_groups.py``.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

from src.access import private_profiles as pp
from src.access import store
from src.access.catalog import resource_ids
from src.access.policy import ProfileAccessDenied, can_use_profile, resolve_profile_for
from src.access.principal import load_principal
from src.agent_profile import profile_manager
from test_access_groups import ADMIN, ANNA, BRUNO, _setup

SKILL_A, SKILL_B = resource_ids("skill")[:2]


@pytest.fixture(autouse=True)
def _reset_overlay():
    """The overlay lives on the global ``profile_manager``: start and end empty."""
    profile_manager.replace_private_profiles([])
    yield
    profile_manager.replace_private_profiles([])


def _chat(row_id: str, ident: str):
    from src.api.auth_login import ChatAuthIdentity

    return ChatAuthIdentity(
        user_row_id=row_id, identifier=ident, via="chat_token", roles=[]
    )


def _body(name="Analisi", skills=(), mcp=(), native=(), instructions="Sii breve."):
    from src.api.v1.me_profiles import ProfileBody

    return ProfileBody(
        name=name,
        description="desc",
        instructions=instructions,
        skills=list(skills),
        mcp_servers=list(mcp),
        native_tool_groups=list(native),
    )


# --- slug and ownership ------------------------------------------------------------


@pytest.mark.anyio
async def test_slug_is_namespaced_unique_and_owner_only(monkeypatch, tmp_path):
    """Two profiles with the same name get distinct ``usr_`` slugs; only the
    owner may use them (not other users, not admins)."""
    await _setup(monkeypatch, tmp_path)
    from src.api.v1 import me_profiles as api

    anna = _chat(ANNA, "anna")
    first = await api.create_my_profile(_body("Mio Assistente"), auth=anna)
    second = await api.create_my_profile(_body("Mio Assistente"), auth=anna)
    assert first["slug"].startswith("usr_") and first["slug"].endswith(
        "_mio_assistente"
    )
    assert second["slug"] == first["slug"] + "_2"

    slug = first["slug"]
    assert profile_manager.private_profile_owner(slug) == ANNA
    assert resolve_profile_for(await load_principal(ANNA), slug) == slug
    for other in (BRUNO, ADMIN):
        principal = await load_principal(other)
        assert not can_use_profile(principal, slug)
        with pytest.raises(ProfileAccessDenied):
            resolve_profile_for(principal, slug)
    # A profile rule on Anna does not hide her own private profiles.
    await store.set_rules(store.SUBJECT_USER, ANNA, {"profile": ["data_agent"]})
    assert resolve_profile_for(await load_principal(ANNA), slug) == slug

    # Other users get a 404, never the content.
    with pytest.raises(HTTPException) as exc:
        await api.get_my_profile(slug, auth=_chat(BRUNO, "bruno"))
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_profiles_listing_includes_own_private_profiles(monkeypatch, tmp_path):
    """``GET /profiles`` = allowed global profiles + the caller's own ones."""
    await _setup(monkeypatch, tmp_path)
    from src.api.main import get_allowed_profiles_for_user
    from src.api.v1 import me_profiles as api

    created = await api.create_my_profile(_body(), auth=_chat(ANNA, "anna"))
    anna_list = await get_allowed_profiles_for_user(ANNA)
    own = [p for p in anna_list if p.get("private")]
    assert [p["slug"] for p in own] == [created["slug"]]
    assert all(not p.get("private") for p in await get_allowed_profiles_for_user(BRUNO))


# --- validation ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_save_rejects_items_not_allowed_and_enforces_limits(
    monkeypatch, tmp_path
):
    """Unknown or not-granted items -> 422; count limit -> 409; capability
    rule "nothing" -> 403 on create."""
    await _setup(monkeypatch, tmp_path)
    from src.api.v1 import me_profiles as api

    anna = _chat(ANNA, "anna")
    await store.set_rules(store.SUBJECT_USER, ANNA, {"skill": [SKILL_A]})
    with pytest.raises(HTTPException) as exc:
        await api.create_my_profile(_body(skills=[SKILL_A, SKILL_B, "nope"]), auth=anna)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "items_not_allowed"
    assert exc.value.detail["items"] == {"skills": [SKILL_B, "nope"]}

    monkeypatch.setenv("AION_PRIVATE_PROFILE_INSTRUCTIONS_MAX", "5")
    with pytest.raises(HTTPException) as exc:
        await api.create_my_profile(_body(instructions="troppo lungo"), auth=anna)
    assert exc.value.status_code == 422
    monkeypatch.delenv("AION_PRIVATE_PROFILE_INSTRUCTIONS_MAX")

    monkeypatch.setenv("AION_PRIVATE_PROFILES_MAX", "1")
    await api.create_my_profile(_body(skills=[SKILL_A]), auth=anna)
    with pytest.raises(HTTPException) as exc:
        await api.create_my_profile(_body("Altro"), auth=anna)
    assert exc.value.status_code == 409

    await store.set_rules(store.SUBJECT_USER, BRUNO, {"capability": []})
    listing = await api.list_my_profiles(auth=_chat(BRUNO, "bruno"))
    assert listing["can_create"] is False
    with pytest.raises(HTTPException) as exc:
        await api.create_my_profile(_body(), auth=_chat(BRUNO, "bruno"))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "private_profiles_not_allowed"


# --- revocation at runtime -------------------------------------------------------------


@pytest.mark.anyio
async def test_revoked_items_disappear_at_runtime_and_come_back(monkeypatch, tmp_path):
    """The agent gets only the items allowed *now*; the editor sees the
    revoked ones as ``unavailable``; re-granting restores them."""
    await _setup(monkeypatch, tmp_path)
    from src.api.v1 import me_profiles as api

    anna = _chat(ANNA, "anna")
    created = await api.create_my_profile(
        _body(skills=[SKILL_A, SKILL_B], mcp=["grafana", "odoo"], native=["mnemos"]),
        auth=anna,
    )
    slug = created["slug"]
    stored = profile_manager.get_profile(slug)

    await store.set_rules(
        store.SUBJECT_USER, ANNA, {"skill": [SKILL_A], "mcp_server": ["odoo"]}
    )
    eff = pp.effective_private_profile(stored, await load_principal(ANNA))
    assert eff.skills == [SKILL_A] and eff.mcp_servers == ["odoo"]
    assert eff.native_tool_groups == ["mnemos"]
    assert stored.skills == [SKILL_A, SKILL_B]  # shared object untouched

    view = await api.get_my_profile(slug, auth=anna)
    assert view["unavailable"] == {
        "skills": [SKILL_B],
        "mcp_servers": ["grafana"],
        "native_tool_groups": [],
    }

    await store.set_rules(store.SUBJECT_USER, ANNA, {"skill": None, "mcp_server": None})
    eff = pp.effective_private_profile(stored, await load_principal(ANNA))
    assert eff.skills == [SKILL_A, SKILL_B] and eff.mcp_servers == ["grafana", "odoo"]


# --- API -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_delete_pauses_jobs_and_duplicate(monkeypatch, tmp_path):
    """Update keeps the slug; delete pauses the cron jobs using the profile;
    duplicate copies only what the caller may add."""
    sm = await _setup(monkeypatch, tmp_path)
    from sqlalchemy import select

    from src.api.v1 import me_profiles as api
    from src.data.models import ScheduledJob

    anna = _chat(ANNA, "anna")
    created = await api.create_my_profile(_body(), auth=anna)
    slug = created["slug"]
    updated = await api.update_my_profile(
        slug, _body("Nuovo nome", skills=[SKILL_A]), auth=anna
    )
    assert updated["slug"] == slug and updated["name"] == "Nuovo nome"
    assert profile_manager.get_profile(slug).skills == [SKILL_A]

    async with sm() as session:
        session.add(
            ScheduledJob(
                job_id="job-1",
                user_id="anna",
                name="daily",
                cron_expression="0 8 * * *",
                profile_slug=slug,
                prompt="ciao",
            )
        )
        await session.commit()
    res = await api.delete_my_profile(slug, auth=anna)
    assert res["paused_jobs"] == 1
    assert profile_manager.get_profile(slug) is None
    async with sm() as session:
        job = (await session.execute(select(ScheduledJob))).scalars().one()
    assert job.enabled is False

    # Duplicate a global profile: MCP servers outside Anna's rule are dropped.
    await store.set_rules(store.SUBJECT_USER, ANNA, {"mcp_server": ["query_memory"]})
    copy = await api.duplicate_profile(
        "data_agent", api.DuplicateBody(name="Copia"), auth=anna
    )
    assert copy["name"] == "Copia" and copy["mcp_servers"] == ["query_memory"]
    assert copy["slug"].startswith("usr_")


@pytest.mark.anyio
async def test_building_blocks_follow_rules(monkeypatch, tmp_path):
    """The editor only offers what the caller may add."""
    await _setup(monkeypatch, tmp_path)
    from src.api.v1 import me_profiles as api

    await store.set_rules(
        store.SUBJECT_USER, ANNA, {"skill": [SKILL_A], "native_tool_group": []}
    )
    blocks = await api.get_building_blocks(auth=_chat(ANNA, "anna"))
    assert [b["id"] for b in blocks["skills"]] == [SKILL_A]
    assert blocks["native_tool_groups"] == []
    assert any(b["id"] == "grafana" for b in blocks["mcp_servers"])


@pytest.mark.anyio
async def test_overlay_reload_and_user_deletion(monkeypatch, tmp_path):
    """Startup reload rebuilds the overlay from the DB; deleting the user
    removes their private profiles from DB and overlay."""
    await _setup(monkeypatch, tmp_path)
    from src.api import admin as admin_api
    from src.api.v1 import me_profiles as api

    created = await api.create_my_profile(_body(), auth=_chat(ANNA, "anna"))
    profile_manager.replace_private_profiles([])
    assert await pp.load_private_profiles() == 1
    assert profile_manager.private_profile_owner(created["slug"]) == ANNA

    await admin_api.delete_user(ANNA)
    assert profile_manager.get_profile(created["slug"]) is None
    assert await pp.load_private_profiles() == 0


@pytest.mark.anyio
async def test_migration_is_idempotent(monkeypatch, tmp_path):
    """k9h0n1f039 runs twice on a DB where create_all already made the table."""
    await _setup(monkeypatch, tmp_path)
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    mod = importlib.import_module("migrations.versions.k9h0n1f039_user_profiles")
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    try:
        for _ in range(2):
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    mod.upgrade()
        assert "user_profiles" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


# --- AI wizard -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wizard_drafts_stay_within_user_rules(monkeypatch, tmp_path):
    """The wizard sees and returns only skills / MCP servers the user may add,
    even if the LLM proposes others; refine keeps allowed native tools; the
    generate step needs the create capability."""
    await _setup(monkeypatch, tmp_path)
    from src.api.v1 import me_profiles as api
    from src.runtime import wizard_generator as wg

    seen = {}

    def fake_llm(system_prompt, prompt, history=None):
        seen["system"] = system_prompt
        return {
            "name": "Report Helper",
            "slug": "ignored",
            "description": "Helps",
            "instructions": "# Role\nHelp.",
            "skills": [SKILL_A, SKILL_B],
            "mcp_servers": ["grafana", "odoo"],
        }

    monkeypatch.setattr(wg, "_call_llm_for_json", fake_llm)
    await store.set_rules(
        store.SUBJECT_USER,
        ANNA,
        {"skill": [SKILL_A], "mcp_server": ["odoo"], "native_tool_group": ["mnemos"]},
    )
    anna = _chat(ANNA, "anna")

    draft = await api.wizard_generate(
        api.WizardGenerateBody(prompt="report"), auth=anna
    )
    assert draft["skills"] == [SKILL_A] and draft["mcp_servers"] == ["odoo"]
    assert "slug" not in draft and draft["native_tool_groups"] == []
    assert f'"{SKILL_B}"' not in seen["system"] and '"grafana"' not in seen["system"]
    # The draft can be created as is.
    created = await api.create_my_profile(api.ProfileBody(**draft), auth=anna)
    assert created["skills"] == [SKILL_A]

    refined = await api.wizard_refine(
        api.WizardRefineBody(
            prompt="more detail",
            current_profile=_body(skills=[SKILL_A], native=["mnemos", "web_research"]),
        ),
        auth=anna,
    )
    assert refined["native_tool_groups"] == ["mnemos"]
    assert refined["mcp_servers"] == ["odoo"]

    await store.set_rules(store.SUBJECT_USER, BRUNO, {"capability": []})
    with pytest.raises(HTTPException) as exc:
        await api.wizard_generate(
            api.WizardGenerateBody(prompt="x"), auth=_chat(BRUNO, "bruno")
        )
    assert exc.value.status_code == 403
