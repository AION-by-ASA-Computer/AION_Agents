"""Pydantic schema and validation for agent profile YAML (P2.1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aion.profile_schema")

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:  # pragma: no cover
    BaseModel = object  # type: ignore[misc, assignment]

    def Field(*a, **k):
        return None

    def field_validator(*a, **k):
        return lambda f: f


class ProfileSchema(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    instructions: str = ""
    skills: List[str] = Field(default_factory=list)
    mcp_servers: List[str] = Field(default_factory=list)
    critical_skills: Optional[List[str]] = None
    native_tool_groups: List[str] = Field(default_factory=list)
    max_agent_steps: Optional[int] = None
    agent: Optional[Dict[str, Any]] = None
    # Allowlist of profile slugs this profile may delegate to via the
    # ``delegate_to_agent`` tool (Claude-style sub-agents). Empty = no delegation.
    # See docs/agents/subagents.md.
    subagents: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return (v or "").strip()

    @classmethod
    def from_yaml_dict(
        cls, data: Optional[Dict[str, Any]], slug: str
    ) -> "ProfileSchema":
        if not data or not isinstance(data, dict):
            raise ValueError(f"profile {slug}: empty or invalid YAML")
        agent_cfg = data.get("agent") or {}
        max_steps_raw = data.get("max_agent_steps")
        if max_steps_raw is None and isinstance(agent_cfg, dict):
            max_steps_raw = agent_cfg.get("max_steps")
        max_agent_steps: Optional[int] = None
        if max_steps_raw is not None:
            try:
                max_agent_steps = max(1, int(max_steps_raw))
            except (TypeError, ValueError):
                max_agent_steps = None
        cs = data.get("critical_skills")
        if "critical_skills" not in data:
            critical_skills = None
        elif cs is None:
            critical_skills = None
        else:
            critical_skills = strip_system_skills(cs)
        return cls(
            name=str(data.get("name") or slug),
            description=str(data.get("description") or ""),
            instructions=str(data.get("instructions") or ""),
            skills=strip_system_skills(data.get("skills")),
            mcp_servers=_drop_retired_mcp_servers(data.get("mcp_servers"), slug),
            critical_skills=critical_skills,
            native_tool_groups=list(data.get("native_tool_groups") or []),
            max_agent_steps=max_agent_steps,
            agent=agent_cfg if isinstance(agent_cfg, dict) else None,
            subagents=normalize_subagent_slugs(data.get("subagents"), self_slug=slug),
        )


# Skill that teaches the parent agent when and how to call ``delegate_to_agent``
# (config_std/skills/delegation.md).
DELEGATION_SKILL = "delegation"

# "System" skills are owned by the runtime, never by admins: their presence in a
# profile is derived from other keys (``delegation`` <=> non-empty
# ``subagents``), they are hidden from the admin skill APIs and cannot be
# edited, renamed or deleted there. Admins only pick sub-agents; the protocol
# that makes delegation work follows automatically and cannot be broken by a
# stray edit. See docs/agents/subagents.md.
SYSTEM_SKILLS = frozenset({DELEGATION_SKILL})


def is_system_skill(name: Any) -> bool:
    """True when ``name`` is a runtime-managed skill (see ``SYSTEM_SKILLS``)."""
    return str(name or "").strip() in SYSTEM_SKILLS


def strip_system_skills(raw: Any) -> List[str]:
    """Return a profile skill list without the runtime-managed skills.

    Used when loading and saving profile YAML: a ``delegation`` entry written by
    hand (or left over from older profiles) is ignored, because only the
    ``subagents`` key decides whether the profile gets it.
    """
    return [s for s in list(raw or []) if not is_system_skill(s)]


# MCP slugs that no longer exist as servers. ``aion_subagents`` was replaced by
# the native ``delegate_to_agent`` tool driven by the ``subagents`` key; an old
# YAML that still lists it must load without "server not found" noise.
RETIRED_PROFILE_MCP_SERVERS = frozenset({"aion_subagents"})


def _drop_retired_mcp_servers(raw: Any, slug: str) -> List[str]:
    """Return ``mcp_servers`` without retired slugs, warning once per profile load."""
    servers = list(raw or [])
    kept = [s for s in servers if s not in RETIRED_PROFILE_MCP_SERVERS]
    if len(kept) != len(servers):
        logger.warning(
            "Profile %s: ignoring retired MCP server(s) %s; use the 'subagents' "
            "key for delegation (docs/agents/subagents.md)",
            slug,
            sorted(set(servers) - set(kept)),
        )
    return kept


def normalize_subagent_slugs(raw: Any, *, self_slug: str = "") -> List[str]:
    """Normalize the ``subagents`` allowlist of a profile YAML.

    Lower-cases and de-duplicates the slugs (order preserved) and drops the
    profile's own slug: a profile delegating to itself would only burn tokens
    and is never useful. Non-list values are treated as "no sub-agents" so a
    malformed key never prevents the profile from loading.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    me = (self_slug or "").strip().lower()
    out: List[str] = []
    for item in raw:
        slug = str(item or "").strip().lower()
        if slug and slug != me and slug not in out:
            out.append(slug)
    return out


@dataclass
class ProfileValidationIssue:
    profile_slug: str
    level: str  # error | warning
    message: str


@dataclass
class ProfileValidationReport:
    issues: List[ProfileValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ProfileValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> List[ProfileValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    def merge(self, other: "ProfileValidationReport") -> None:
        self.issues.extend(other.issues)


def validate_profile_references(
    schema: ProfileSchema,
    slug: str,
    *,
    skill_exists,
    server_exists,
    profile_exists=None,
) -> ProfileValidationReport:
    """Check skill, MCP server and sub-agent references (warnings only).

    ``profile_exists`` is optional so existing callers keep working; when given,
    every slug in ``subagents`` must resolve to a loaded profile.
    """
    report = ProfileValidationReport()
    for skill in schema.skills or []:
        if skill and not ("*" in skill or "?" in skill) and not skill_exists(skill):
            report.issues.append(
                ProfileValidationIssue(
                    slug,
                    "warning",
                    f"skill '{skill}' not found in registry",
                )
            )
    for server in schema.mcp_servers or []:
        if server and not server_exists(server):
            report.issues.append(
                ProfileValidationIssue(
                    slug,
                    "warning",
                    f"MCP server '{server}' not found in registry",
                )
            )
    if profile_exists is not None:
        for sub in schema.subagents or []:
            if not profile_exists(sub):
                report.issues.append(
                    ProfileValidationIssue(
                        slug,
                        "warning",
                        f"sub-agent profile '{sub}' not found",
                    )
                )
    return report


def log_validation_report(report: ProfileValidationReport) -> None:
    for issue in report.warnings:
        logger.warning(
            "Profile validation [%s] %s: %s",
            issue.profile_slug,
            issue.level,
            issue.message,
        )
    for issue in report.errors:
        logger.error(
            "Profile validation [%s] %s: %s",
            issue.profile_slug,
            issue.level,
            issue.message,
        )
