"""The ``delegation`` skill is a runtime-managed "system skill".

Covers the behaviour documented in docs/agents/subagents.md ("The delegation
skill is automatic"): a profile gets the skill, inlined in full, exactly when it
has sub-agents; a YAML entry is ignored and stripped on save; the admin skill API
neither shows nor lets anyone edit it; the registry only loads the shipped copy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

import src.agent_profile as ap
import src.api.admin as admin
from src.agent_profile import AgentProfile
from src.api.admin import ProfileUpdate, SkillRename, SkillUpdate
from src.runtime.profile_schema import (
    DELEGATION_SKILL,
    ProfileSchema,
    is_system_skill,
    strip_system_skills,
)
from src.skill_registry import SkillRegistry, skill_registry

DELEGATION_HEADER = "### Protocol rules (delegation)"


@pytest.fixture(autouse=True)
def prompt_env(monkeypatch):
    """Index prompt mode (the default) without SOUL/MEMORY/USER noise."""
    monkeypatch.setenv("AION_SKILL_SYSTEM_PROMPT_MODE", "index")
    monkeypatch.setenv("AION_SOUL_MEMORY_USER_SPLIT", "0")


def _profile(*, skills=None, subagents=None, critical_skills=None) -> AgentProfile:
    return AgentProfile(
        name="T",
        description="",
        instructions="Hi",
        skills=list(skills or []),
        critical_skills=critical_skills,
        slug="t",
        subagents=subagents,
    )


# --- Schema helpers -------------------------------------------------------


def test_strip_system_skills_removes_only_delegation():
    """Only runtime-managed names are dropped; order of the others is kept."""
    assert is_system_skill(DELEGATION_SKILL)
    assert not is_system_skill("core_protocol")
    assert strip_system_skills(["a", "delegation", "b"]) == ["a", "b"]
    assert strip_system_skills(None) == []


def test_yaml_entry_is_ignored_on_load():
    """A hand-written ``delegation`` in skills/critical_skills does not survive loading."""
    schema = ProfileSchema.from_yaml_dict(
        {
            "name": "X",
            "skills": ["delegation", "pdf"],
            "critical_skills": ["delegation"],
        },
        "x",
    )
    assert schema.skills == ["pdf"]
    assert schema.critical_skills == []


# --- Runtime injection ----------------------------------------------------


def test_profile_with_subagents_gets_skill_inlined():
    """Sub-agents => delegation is active and inlined in full, even with explicit critical_skills."""
    p = _profile(
        skills=["pdf"],
        subagents=["graphic_designer"],
        critical_skills=["core_protocol"],
    )
    assert p.resolved_skills()[-1] == DELEGATION_SKILL
    assert DELEGATION_SKILL in p._resolved_critical_skill_names()
    assert DELEGATION_HEADER in p.generate_system_prompt()


def test_profile_without_subagents_never_gets_skill():
    """No sub-agents => no delegation, even if listed by hand or matched by a glob."""
    p = _profile(skills=["delegation", "deleg*"])
    assert DELEGATION_SKILL not in p.resolved_skills()
    assert DELEGATION_SKILL not in p._resolved_critical_skill_names()
    assert DELEGATION_HEADER not in p.generate_system_prompt()


def test_skill_is_not_duplicated():
    """Listing the skill and having sub-agents still yields a single entry."""
    p = _profile(skills=["delegation"], subagents=["x"])
    assert p.resolved_skills().count(DELEGATION_SKILL) == 1


# --- Registry -------------------------------------------------------------


def test_registry_loads_system_skill_only_from_shipped_dir(tmp_path: Path):
    """A local/generated copy (even with a higher version) never overrides the shipped one."""
    shipped = tmp_path / "std"
    local = tmp_path / "local"
    generated = tmp_path / "gen"
    for d in (shipped, local, generated):
        d.mkdir()
    (shipped / "delegation.md").write_text(
        "---\nname: delegation\nversion: 1\n---\nSHIPPED", encoding="utf-8"
    )
    (local / "delegation.md").write_text(
        "---\nname: delegation\nversion: 9\n---\nLOCAL", encoding="utf-8"
    )
    (generated / "delegation.md").write_text(
        "---\nname: delegation\nversion: 9\n---\nGEN", encoding="utf-8"
    )
    # A hidden list naming the skill must not remove it either.
    (tmp_path / "hidden_skills.json").write_text('["delegation"]', encoding="utf-8")

    reg = SkillRegistry(
        curated_dir=str(local),
        curated_fallback_dir=str(shipped),
        generated_dir=str(generated),
    )
    assert reg.get_skill_full("delegation").strip() == "SHIPPED"


# --- Admin skill API --------------------------------------------------------


def test_admin_skill_list_hides_delegation():
    """GET /admin/skills does not list the system skill."""
    assert skill_registry.skill_exists(DELEGATION_SKILL)
    listed = asyncio.run(admin.list_skills(status=None))
    assert DELEGATION_SKILL not in listed
    assert "artifact_protocol" in listed


@pytest.mark.parametrize(
    "call, code",
    [
        (lambda: admin.get_skill(DELEGATION_SKILL), 404),
        (lambda: admin.export_skill_md(DELEGATION_SKILL), 404),
        (lambda: admin.promote_skill(DELEGATION_SKILL), 404),
        (
            lambda: admin.rename_skill(
                DELEGATION_SKILL, SkillRename(new_name="x_deleg")
            ),
            404,
        ),
        (
            lambda: admin.rename_skill(
                "core_protocol", SkillRename(new_name=DELEGATION_SKILL)
            ),
            403,
        ),
        (
            lambda: admin.save_skill(SkillUpdate(name=DELEGATION_SKILL, content="x")),
            403,
        ),
        (
            lambda: admin.save_skill(
                SkillUpdate(name="other", content="---\nname: delegation\n---\nx")
            ),
            403,
        ),
        (lambda: admin.delete_skill(DELEGATION_SKILL), 403),
    ],
)
def test_admin_skill_api_refuses_system_skill(call, code):
    """Reads answer 404 (the skill does not exist for admins), writes 403, before touching disk."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call())
    assert exc.value.status_code == code


# --- Admin profile save -----------------------------------------------------


@pytest.fixture
def pm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ap.ProfileManager:
    """Profile manager on a temp dir with a parent profile and a sub-agent profile."""
    for slug in ("parent", "child"):
        (tmp_path / f"{slug}.yaml").write_text(
            yaml.dump(
                {"name": slug, "instructions": "x", "skills": [], "mcp_servers": []}
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(ap, "migrate_profiles_to_write_dir", lambda: None)
    mgr = ap.ProfileManager(str(tmp_path))
    monkeypatch.setattr(admin, "profile_manager", mgr)
    import src.runtime.profile_sync_state as pss

    monkeypatch.setattr(pss, "record_profile_after_admin_save", lambda _p: None)
    return mgr


def test_save_profile_never_writes_delegation(pm: ap.ProfileManager):
    """Adding a sub-agent activates the skill at runtime, but the YAML stays free of it."""
    asyncio.run(
        admin.save_profile(
            ProfileUpdate(
                slug="parent",
                name="parent",
                description="",
                instructions="x",
                skills=["delegation", "pdf"],
                critical_skills=["delegation"],
                mcp_servers=[],
                subagents=["child"],
            )
        )
    )
    data = yaml.safe_load((pm.write_path / "parent.yaml").read_text(encoding="utf-8"))
    assert data["skills"] == ["pdf"]
    assert data["critical_skills"] == []
    assert data["subagents"] == ["child"]
    assert DELEGATION_SKILL in pm.get_profile("parent").resolved_skills()
