"""Private agent profiles of end users (access control, Phase 3).

A user (or a service account) can build their own profiles from the skills,
MCP servers and native tool groups their access rules allow. This module is
the only place that reads and writes the ``user_profiles`` table.

Key rules (see ``PLAN_access_control.md``, Phase 3):

- **Slug**: generated here, never chosen by the client:
  ``usr_<owner hash>_<normalized name>``. The ``usr_`` prefix keeps private
  profiles apart from the global YAML profiles (now and in the future); a
  numeric suffix (``_2``, ``_3``) keeps the slug unique per tenant.
- **Ownership**: only the owner may use, read or change a private profile
  (checked by ``src.access.policy`` and by the ``/v1/me/profiles`` API).
- **Validation on save**: every skill / MCP server / native tool group must
  exist and be allowed by the owner's rules; limits on count and size.
- **Revocation at runtime**: ``effective_private_profile`` intersects the
  stored lists with the owner's *current* rules every time the agent is
  built, so a revoked skill or MCP server disappears at once and comes back
  if re-granted. The editor shows such items as unavailable
  (``unavailable`` in ``profile_to_dict``) and drops them on the next save.
- **Capability**: creating profiles needs ``capability:private_profiles.create``
  (allowed to everyone while no rule restricts it, decision D2). Existing
  profiles stay usable and editable if the capability is later removed.

Private profiles are mirrored in memory in ``profile_manager`` (overlay) so
the many call sites that resolve a profile by slug (agent factory, pending
integrations, cron, channels) work unchanged. The overlay is loaded at API
startup (``load_private_profiles``) and updated by every write here.

Callers: ``src/api/v1/me_profiles.py`` (CRUD), ``src/main.get_agent``
(runtime filter), ``src/access/policy.py`` (ownership), ``GET /profiles``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import OperationalError, ProgrammingError

from src.access.catalog import resource_ids
from src.access.policy import (
    RESOURCE_CAPABILITY,
    RESOURCE_MCP_SERVER,
    RESOURCE_NATIVE_TOOL_GROUP,
    RESOURCE_SKILL,
    can,
    filter_allowed,
)
from src.access.principal import Principal
from src.data.engine import get_async_session_maker
from src.data.ids import new_uuid7_str
from src.data.models import ScheduledJob, UserProfile

logger = logging.getLogger("aion.access.private_profiles")

PRIVATE_PREFIX = "usr_"
CAPABILITY_CREATE = "private_profiles.create"

# The three lists a private profile is made of: field name -> resource type.
_PARTS = {
    "skills": RESOURCE_SKILL,
    "mcp_servers": RESOURCE_MCP_SERVER,
    "native_tool_groups": RESOURCE_NATIVE_TOOL_GROUP,
}
# Upper bound per list: far above real use, stops oversized payloads.
MAX_ITEMS_PER_LIST = 100
MAX_NAME = 128
MAX_DESCRIPTION = 1000


def _tenant(tenant_id: Optional[str] = None) -> str:
    raw = tenant_id or os.getenv("AION_DEFAULT_TENANT_ID") or "default"
    return raw.strip() or "default"


def max_profiles_per_user() -> int:
    """``AION_PRIVATE_PROFILES_MAX`` (default 10, decision D3)."""
    try:
        return max(0, int(os.getenv("AION_PRIVATE_PROFILES_MAX", "10")))
    except ValueError:
        return 10


def max_instructions_chars() -> int:
    """``AION_PRIVATE_PROFILE_INSTRUCTIONS_MAX`` (default 20000, decision D3)."""
    try:
        return max(0, int(os.getenv("AION_PRIVATE_PROFILE_INSTRUCTIONS_MAX", "20000")))
    except ValueError:
        return 20000


def is_private_slug(slug: Optional[str]) -> bool:
    """True for slugs in the private namespace (existing or not)."""
    return (slug or "").strip().lower().startswith(PRIVATE_PREFIX)


# --- errors --------------------------------------------------------------------


class PrivateProfileError(Exception):
    """Base error; ``code`` is the API error code, ``extra`` goes in the body."""

    status = 422
    code = "invalid_profile"

    def __init__(self, message: str = "", **extra: Any):
        super().__init__(message or self.code)
        self.extra = extra


class ProfileNotOwned(PrivateProfileError):
    """No private profile with that slug owned by the caller."""

    status = 404
    code = "profile_not_found"


class CreateNotAllowed(PrivateProfileError):
    """The caller's rules exclude ``capability:private_profiles.create``."""

    status = 403
    code = "private_profiles_not_allowed"


class LimitReached(PrivateProfileError):
    """The caller already has ``AION_PRIVATE_PROFILES_MAX`` profiles."""

    status = 409
    code = "private_profiles_limit"


class InvalidContent(PrivateProfileError):
    """Missing name, text too long or items not allowed / unknown."""


# --- data ----------------------------------------------------------------------


@dataclass
class ProfileInput:
    """Editable fields of a private profile (create or full update)."""

    name: str
    description: str = ""
    instructions: str = ""
    skills: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)
    native_tool_groups: List[str] = field(default_factory=list)


def _load_list(raw: Optional[str]) -> List[str]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data if x] if isinstance(data, list) else []


def _dedupe(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    for i in items:
        s = str(i).strip()
        if s and s not in out:
            out.append(s)
    return out


def _to_agent_profile(row: UserProfile):
    """``AgentProfile`` for the agent factory, with the *stored* lists.

    ``critical_skills=None`` keeps the system defaults: users cannot change
    which skills are always loaded.
    """
    from src.agent_profile import AgentProfile

    return AgentProfile(
        name=row.name,
        description=row.description or "",
        instructions=row.instructions or "",
        skills=_load_list(row.skills_json),
        mcp_servers=_load_list(row.mcp_servers_json),
        slug=row.slug,
        critical_skills=None,
        native_tool_groups=_load_list(row.native_tool_groups_json),
    )


def _unavailable(principal: Principal, row: UserProfile) -> Dict[str, List[str]]:
    """Stored items the owner may no longer use (revoked or removed)."""
    out: Dict[str, List[str]] = {}
    for part, rt in _PARTS.items():
        stored = _load_list(getattr(row, f"{part}_json"))
        known = set(resource_ids(rt))
        usable = set(filter_allowed(principal, rt, [s for s in stored if s in known]))
        out[part] = [s for s in stored if s not in usable]
    return out


def profile_to_dict(row: UserProfile, principal: Principal) -> Dict[str, Any]:
    """API view of a private profile, with the items no longer usable."""
    return {
        "slug": row.slug,
        "name": row.name,
        "description": row.description or "",
        "instructions": row.instructions or "",
        "skills": _load_list(row.skills_json),
        "mcp_servers": _load_list(row.mcp_servers_json),
        "native_tool_groups": _load_list(row.native_tool_groups_json),
        "unavailable": _unavailable(principal, row),
        "private": True,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- overlay ---------------------------------------------------------------------


async def load_private_profiles(tenant_id: Optional[str] = None) -> int:
    """Load every private profile of the tenant into ``profile_manager``.

    Called once at API startup. Returns the number of profiles; a missing
    table (DB not migrated yet) yields 0 and a warning, never a crash.
    """
    from src.agent_profile import profile_manager

    try:
        async with get_async_session_maker()() as session:
            rows = (
                (
                    await session.execute(
                        select(UserProfile).where(
                            UserProfile.tenant_id == _tenant(tenant_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
    except (OperationalError, ProgrammingError):
        logger.warning("user_profiles table missing: private profiles not loaded")
        return 0
    profile_manager.replace_private_profiles(
        [(_to_agent_profile(r), r.owner_user_id) for r in rows]
    )
    return len(rows)


def effective_private_profile(profile: Any, principal: Principal) -> Any:
    """Copy of a private profile limited to what the owner may use *now*.

    Items no longer allowed (or no longer existing) are dropped; the shared
    object in ``profile_manager`` is never mutated. Because the filtered lists
    feed ``profile_sig`` in ``get_agent``, a revocation also yields a new
    agent-cache entry.
    """
    out = copy.copy(profile)
    for part, rt in _PARTS.items():
        stored = list(getattr(profile, part, None) or [])
        known = set(resource_ids(rt))
        setattr(
            out, part, filter_allowed(principal, rt, [s for s in stored if s in known])
        )
    return out


# --- validation ---------------------------------------------------------------------


def _validate(principal: Principal, data: ProfileInput) -> ProfileInput:
    """Normalized input or ``InvalidContent`` with the offending fields / ids."""
    name = (data.name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise InvalidContent("name is required (max 128 chars)", field="name")
    description = (data.description or "").strip()
    if len(description) > MAX_DESCRIPTION:
        raise InvalidContent("description too long", field="description")
    instructions = data.instructions or ""
    if len(instructions) > max_instructions_chars():
        raise InvalidContent(
            "instructions too long",
            field="instructions",
            max=max_instructions_chars(),
        )
    clean: Dict[str, List[str]] = {}
    not_allowed: Dict[str, List[str]] = {}
    for part, rt in _PARTS.items():
        items = _dedupe(getattr(data, part) or [])
        if len(items) > MAX_ITEMS_PER_LIST:
            raise InvalidContent(f"too many {part}", field=part)
        known = set(resource_ids(rt))
        allowed = set(filter_allowed(principal, rt, [i for i in items if i in known]))
        bad = [i for i in items if i not in allowed]
        if bad:
            not_allowed[part] = bad
        clean[part] = items
    if not_allowed:
        # Unknown and revoked ids are reported together: for the user both
        # mean "you cannot add this".
        raise InvalidContent(
            "items not allowed", code="items_not_allowed", items=not_allowed
        )
    return ProfileInput(
        name=name, description=description, instructions=instructions, **clean
    )


# --- CRUD ------------------------------------------------------------------------


def _owner_tag(owner_user_id: str) -> str:
    """Short stable hash of the owner id (keeps slugs short and opaque)."""
    return hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:6]


async def _unique_slug(session, tenant: str, owner_user_id: str, name: str) -> str:
    """``usr_<owner>_<name>``, with ``_2``, ``_3``… if already taken."""
    from src.agent_profile import normalize_profile_slug

    base = f"{PRIVATE_PREFIX}{_owner_tag(owner_user_id)}_"
    base += (normalize_profile_slug(name) or "profile")[:80]
    taken = set(
        (
            await session.execute(
                select(UserProfile.slug).where(
                    UserProfile.tenant_id == tenant,
                    UserProfile.slug.like(f"{base}%"),
                )
            )
        )
        .scalars()
        .all()
    )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


async def list_owned(principal: Principal) -> List[UserProfile]:
    """The caller's private profiles, most recently updated first.

    ``[]`` when the table does not exist yet (DB not migrated): profile
    pickers (``GET /profiles``) must keep working on such databases.
    """
    if not principal.user_row_id:
        return []
    try:
        return await _select_owned(principal)
    except (OperationalError, ProgrammingError):
        logger.warning("user_profiles table missing: no private profiles listed")
        return []


async def _select_owned(principal: Principal) -> List[UserProfile]:
    async with get_async_session_maker()() as session:
        rows = (
            (
                await session.execute(
                    select(UserProfile)
                    .where(
                        UserProfile.tenant_id == _tenant(principal.tenant_id),
                        UserProfile.owner_user_id == principal.user_row_id,
                    )
                    .order_by(UserProfile.updated_at.desc(), UserProfile.name)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def get_owned(principal: Principal, slug: str) -> UserProfile:
    """One private profile of the caller, else ``ProfileNotOwned``."""
    async with get_async_session_maker()() as session:
        row = (
            (
                await session.execute(
                    select(UserProfile).where(
                        UserProfile.tenant_id == _tenant(principal.tenant_id),
                        UserProfile.slug == (slug or "").strip().lower(),
                    )
                )
            )
            .scalars()
            .first()
        )
    if row is None or row.owner_user_id != principal.user_row_id:
        raise ProfileNotOwned()
    return row


def can_create(principal: Principal) -> bool:
    """Capability check (no ``capability`` rule = allowed, decision D2)."""
    return bool(principal.user_row_id) and can(
        principal, "use", RESOURCE_CAPABILITY, CAPABILITY_CREATE
    )


async def create(principal: Principal, data: ProfileInput) -> UserProfile:
    """Create a private profile for the caller and add it to the overlay."""
    from src.agent_profile import profile_manager

    if not can_create(principal):
        raise CreateNotAllowed()
    clean = _validate(principal, data)
    tenant = _tenant(principal.tenant_id)
    async with get_async_session_maker()() as session:
        count = len(
            (
                await session.execute(
                    select(UserProfile.id).where(
                        UserProfile.tenant_id == tenant,
                        UserProfile.owner_user_id == principal.user_row_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if count >= max_profiles_per_user():
            raise LimitReached(max=max_profiles_per_user())
        row = UserProfile(
            id=new_uuid7_str(),
            tenant_id=tenant,
            owner_user_id=principal.user_row_id,
            slug=await _unique_slug(session, tenant, principal.user_row_id, clean.name),
            name=clean.name,
            description=clean.description,
            instructions=clean.instructions,
            skills_json=json.dumps(clean.skills),
            mcp_servers_json=json.dumps(clean.mcp_servers),
            native_tool_groups_json=json.dumps(clean.native_tool_groups),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    profile_manager.put_private_profile(_to_agent_profile(row), row.owner_user_id)
    return row


async def update(principal: Principal, slug: str, data: ProfileInput) -> UserProfile:
    """Replace the editable fields; the slug never changes (cron jobs and
    conversations keep pointing at it)."""
    from src.agent_profile import profile_manager

    current = await get_owned(principal, slug)
    clean = _validate(principal, data)
    async with get_async_session_maker()() as session:
        row = await session.get(UserProfile, current.id)
        row.name = clean.name
        row.description = clean.description
        row.instructions = clean.instructions
        row.skills_json = json.dumps(clean.skills)
        row.mcp_servers_json = json.dumps(clean.mcp_servers)
        row.native_tool_groups_json = json.dumps(clean.native_tool_groups)
        await session.commit()
        await session.refresh(row)
    profile_manager.put_private_profile(_to_agent_profile(row), row.owner_user_id)
    return row


async def delete_owned(principal: Principal, slug: str) -> int:
    """Delete a private profile; pause the cron jobs that used it.

    Returns how many jobs were paused. Conversations need nothing: a deleted
    private slug is unknown, so the agent factory falls back to the default
    profile (``ProfileManager.resolve_profile``).
    """
    from src.agent_profile import profile_manager

    row = await get_owned(principal, slug)
    async with get_async_session_maker()() as session:
        paused = await session.execute(
            sql_update(ScheduledJob)
            .where(
                ScheduledJob.profile_slug == row.slug,
                ScheduledJob.enabled.is_(True),
            )
            .values(enabled=False)
        )
        await session.execute(delete(UserProfile).where(UserProfile.id == row.id))
        await session.commit()
    profile_manager.drop_private_profile(row.slug)
    return int(paused.rowcount or 0)


async def delete_all_for_user(user_row_id: str) -> None:
    """Remove a deleted user's private profiles (the FK cascade is not
    guaranteed on SQLite). Called by ``DELETE /admin/users/{id}``."""
    from src.agent_profile import profile_manager

    async with get_async_session_maker()() as session:
        await session.execute(
            delete(UserProfile).where(UserProfile.owner_user_id == user_row_id)
        )
        await session.commit()
    # By owner, not by the deleted rows: the FK cascade may already have
    # removed them together with the user.
    profile_manager.drop_private_profiles_of(user_row_id)


def duplicate_input(principal: Principal, source: Any, name: str) -> ProfileInput:
    """Input for a new private profile copied from ``source`` (an
    ``AgentProfile`` the caller may use): only the items the caller may add
    are copied; glob skills of global profiles are expanded first."""
    skills = source.resolved_skills() if hasattr(source, "resolved_skills") else []
    data = {
        "skills": skills,
        "mcp_servers": list(getattr(source, "mcp_servers", None) or []),
        "native_tool_groups": list(getattr(source, "native_tool_groups", None) or []),
    }
    for part, rt in _PARTS.items():
        known = set(resource_ids(rt))
        data[part] = filter_allowed(
            principal, rt, [i for i in data[part] if i in known]
        )
    return ProfileInput(
        name=name,
        description=getattr(source, "description", "") or "",
        instructions=(getattr(source, "instructions", "") or "")[
            : max_instructions_chars()
        ],
        **data,
    )
