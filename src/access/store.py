"""Persistence for user groups, memberships and access rules (Phase 1).

All writes to ``user_groups``, ``user_group_members`` and ``access_grants`` go
through this module so that every change also invalidates the cached
principals of the affected users (``src.access.principal``). Callers (the
``/admin/groups`` and ``/admin/users/{id}/access`` endpoints) add auditing.

Rule model (see ``PLAN_access_control.md`` principle 3): for each
``(resource_type, action)`` a subject (user or group) either has a rule or not.

- no rows               -> no rule (the subject does not restrict that type)
- rows with ids / ``*`` -> rule allowing those ids (``*`` = all)
- one ``!none`` row     -> rule allowing nothing

In the API a rule is ``list[str]`` (``[]`` = nothing) and "no rule" is
``None`` / absent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import delete, func, select

from src.access.principal import invalidate_principal
from src.data.engine import get_async_session_maker
from src.data.ids import new_uuid7_str
from src.data.models import (
    AccessGrant,
    User,
    UserGroup,
    UserGroupMember,
    UserProfileAccess,
)

logger = logging.getLogger("aion.access.store")

SUBJECT_USER = "user"
SUBJECT_GROUP = "group"
WILDCARD = "*"
# Stored marker for "has a rule for this type, granting nothing".
NONE_MARKER = "!none"

# Resource types an admin may set rules for through the Phase 1 API. Phase 2
# adds skill / mcp_server / native_tool_group / capability, Phase 5 admin_section.
EDITABLE_RESOURCE_TYPES = ("profile",)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GroupNotFound(Exception):
    """No group with that id in the tenant."""


class GroupSlugTaken(Exception):
    """Another group of the tenant already uses that slug."""


class InvalidSlug(ValueError):
    """Group slug outside ``[a-z0-9][a-z0-9_-]{0,63}``."""


@dataclass(frozen=True)
class GroupInfo:
    """Group row plus its member count, as returned to the admin API."""

    id: str
    slug: str
    name: str
    description: Optional[str]
    member_count: int


def normalize_rule(entries: Optional[Iterable[str]]) -> Optional[List[str]]:
    """Canonical form of a rule: ``None`` (no rule), ``["*"]``, sorted unique ids
    or ``[]`` (nothing). Blank entries and the internal marker are dropped."""
    if entries is None:
        return None
    seen: List[str] = []
    for raw in entries:
        e = str(raw or "").strip()
        if not e or e == NONE_MARKER or e in seen:
            continue
        seen.append(e)
    if WILDCARD in seen:
        return [WILDCARD]
    return seen


def _rows_to_rules(rows: Iterable[AccessGrant]) -> Dict[str, List[str]]:
    """``{resource_type: [ids]}`` from grant rows (single action), keeping order."""
    rules: Dict[str, List[str]] = {}
    for r in rows:
        ids = rules.setdefault(r.resource_type, [])
        if r.resource_id != NONE_MARKER and r.resource_id not in ids:
            ids.append(r.resource_id)
    return rules


# --- rules -------------------------------------------------------------------


async def get_rules(
    subject_type: str,
    subject_id: str,
    *,
    tenant_id: str = "default",
    action: str = "use",
) -> Dict[str, List[str]]:
    """Rules of one subject for ``action``: ``{resource_type: [ids]}``.

    Types without a rule are absent. For users, legacy ``user_profile_access``
    rows count as a direct ``profile`` rule until that table is dropped.
    """
    async with get_async_session_maker()() as session:
        rows = (
            (
                await session.execute(
                    select(AccessGrant)
                    .where(
                        AccessGrant.tenant_id == tenant_id,
                        AccessGrant.subject_type == subject_type,
                        AccessGrant.subject_id == subject_id,
                        AccessGrant.action == action,
                    )
                    .order_by(AccessGrant.id)
                )
            )
            .scalars()
            .all()
        )
        rules = _rows_to_rules(rows)
        if subject_type == SUBJECT_USER and action == "use":
            legacy = (
                await session.execute(
                    select(UserProfileAccess.profile_slug)
                    .where(UserProfileAccess.user_id == subject_id)
                    .order_by(UserProfileAccess.id)
                )
            ).scalars()
            for slug in legacy:
                ids = rules.setdefault("profile", [])
                if slug not in ids:
                    ids.append(slug)
    return rules


async def set_rules(
    subject_type: str,
    subject_id: str,
    rules: Dict[str, Optional[Sequence[str]]],
    *,
    tenant_id: str = "default",
    action: str = "use",
    actor: Optional[str] = None,
) -> None:
    """Replace the rules of a subject for the resource types present in ``rules``.

    ``None`` removes the rule (no limit from this subject), ``[]`` stores a
    "nothing" rule. Types not in ``rules`` are left untouched. Setting a user's
    ``profile`` rule also deletes their legacy ``user_profile_access`` rows.
    Invalidates the cached principals of the affected users.
    """
    async with get_async_session_maker()() as session:
        for resource_type, raw in rules.items():
            await session.execute(
                delete(AccessGrant).where(
                    AccessGrant.tenant_id == tenant_id,
                    AccessGrant.subject_type == subject_type,
                    AccessGrant.subject_id == subject_id,
                    AccessGrant.resource_type == resource_type,
                    AccessGrant.action == action,
                )
            )
            if (
                subject_type == SUBJECT_USER
                and resource_type == "profile"
                and action == "use"
            ):
                await session.execute(
                    delete(UserProfileAccess).where(
                        UserProfileAccess.user_id == subject_id
                    )
                )
            entries = normalize_rule(raw)
            if entries is None:
                continue
            for rid in entries or [NONE_MARKER]:
                session.add(
                    AccessGrant(
                        tenant_id=tenant_id,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        resource_type=resource_type,
                        resource_id=rid,
                        action=action,
                        created_by=actor,
                    )
                )
        await session.commit()
    await _invalidate_subject(subject_type, subject_id)


async def _invalidate_subject(subject_type: str, subject_id: str) -> None:
    """Drop cached principals of a user, or of every member of a group."""
    if subject_type == SUBJECT_USER:
        invalidate_principal(subject_id)
        return
    for uid in await list_member_ids(subject_id):
        invalidate_principal(uid)


# --- groups ------------------------------------------------------------------


async def list_groups(tenant_id: str = "default") -> List[GroupInfo]:
    """All groups of the tenant with member counts, sorted by name."""
    async with get_async_session_maker()() as session:
        counts = dict(
            (
                await session.execute(
                    select(UserGroupMember.group_id, func.count())
                    .where(UserGroupMember.tenant_id == tenant_id)
                    .group_by(UserGroupMember.group_id)
                )
            ).all()
        )
        groups = (
            (
                await session.execute(
                    select(UserGroup).where(UserGroup.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
    out = [
        GroupInfo(g.id, g.slug, g.name, g.description, int(counts.get(g.id, 0)))
        for g in groups
    ]
    return sorted(out, key=lambda g: g.name.lower())


async def get_group(group_id: str, tenant_id: str = "default") -> GroupInfo:
    """One group; ``GroupNotFound`` if missing in the tenant."""
    for g in await list_groups(tenant_id):
        if g.id == group_id:
            return g
    raise GroupNotFound(group_id)


def _check_slug(slug: str) -> str:
    s = (slug or "").strip().lower()
    if not _SLUG_RE.match(s):
        raise InvalidSlug(slug)
    return s


async def _slug_in_use(
    session, tenant_id: str, slug: str, exclude_id: str = ""
) -> bool:
    row = (
        await session.execute(
            select(UserGroup.id).where(
                UserGroup.tenant_id == tenant_id,
                UserGroup.slug == slug,
                UserGroup.id != exclude_id,
            )
        )
    ).first()
    return row is not None


async def create_group(
    *,
    slug: str,
    name: str,
    description: Optional[str] = None,
    tenant_id: str = "default",
    actor: Optional[str] = None,
) -> GroupInfo:
    """Create an empty group (no members, no rules). Raises ``InvalidSlug`` /
    ``GroupSlugTaken``."""
    s = _check_slug(slug)
    async with get_async_session_maker()() as session:
        if await _slug_in_use(session, tenant_id, s):
            raise GroupSlugTaken(s)
        g = UserGroup(
            id=new_uuid7_str(),
            tenant_id=tenant_id,
            slug=s,
            name=(name or "").strip() or s,
            description=(description or "").strip() or None,
            created_by=actor,
        )
        session.add(g)
        await session.commit()
        return GroupInfo(g.id, g.slug, g.name, g.description, 0)


async def update_group(
    group_id: str,
    *,
    slug: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    tenant_id: str = "default",
) -> GroupInfo:
    """Rename / re-describe a group. Membership and rules are unchanged."""
    async with get_async_session_maker()() as session:
        g = await session.get(UserGroup, group_id)
        if g is None or g.tenant_id != tenant_id:
            raise GroupNotFound(group_id)
        if slug is not None:
            s = _check_slug(slug)
            if await _slug_in_use(session, tenant_id, s, exclude_id=group_id):
                raise GroupSlugTaken(s)
            g.slug = s
        if name is not None and name.strip():
            g.name = name.strip()
        if description is not None:
            g.description = description.strip() or None
        await session.commit()
    # Group slugs are part of the principal (``groups``).
    await _invalidate_subject(SUBJECT_GROUP, group_id)
    return await get_group(group_id, tenant_id)


async def delete_group(group_id: str, tenant_id: str = "default") -> None:
    """Delete a group with its memberships and rules (members lose its limits)."""
    member_ids = await list_member_ids(group_id)
    async with get_async_session_maker()() as session:
        g = await session.get(UserGroup, group_id)
        if g is None or g.tenant_id != tenant_id:
            raise GroupNotFound(group_id)
        await session.execute(
            delete(AccessGrant).where(
                AccessGrant.subject_type == SUBJECT_GROUP,
                AccessGrant.subject_id == group_id,
            )
        )
        await session.execute(
            delete(UserGroupMember).where(UserGroupMember.group_id == group_id)
        )
        await session.delete(g)
        await session.commit()
    for uid in member_ids:
        invalidate_principal(uid)


# --- memberships -------------------------------------------------------------


async def list_member_ids(group_id: str) -> List[str]:
    """``users.id`` of the members of a group (any tenant check is the caller's)."""
    async with get_async_session_maker()() as session:
        return list(
            (
                await session.execute(
                    select(UserGroupMember.user_id)
                    .where(UserGroupMember.group_id == group_id)
                    .order_by(UserGroupMember.id)
                )
            ).scalars()
        )


async def set_members(
    group_id: str,
    user_ids: Sequence[str],
    *,
    tenant_id: str = "default",
    actor: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Replace the members of a group. Unknown user ids are rejected with
    ``ValueError``. Returns ``{"added": [...], "removed": [...]}``."""
    wanted = list(dict.fromkeys(u.strip() for u in user_ids if u and u.strip()))
    async with get_async_session_maker()() as session:
        g = await session.get(UserGroup, group_id)
        if g is None or g.tenant_id != tenant_id:
            raise GroupNotFound(group_id)
        if wanted:
            found = set(
                (
                    await session.execute(
                        select(User.id).where(
                            User.id.in_(wanted), User.tenant_id == tenant_id
                        )
                    )
                ).scalars()
            )
            missing = [u for u in wanted if u not in found]
            if missing:
                raise ValueError(f"unknown users: {', '.join(missing)}")
        current = set(
            (
                await session.execute(
                    select(UserGroupMember.user_id).where(
                        UserGroupMember.group_id == group_id
                    )
                )
            ).scalars()
        )
        added = [u for u in wanted if u not in current]
        removed = sorted(current - set(wanted))
        if removed:
            await session.execute(
                delete(UserGroupMember).where(
                    UserGroupMember.group_id == group_id,
                    UserGroupMember.user_id.in_(removed),
                )
            )
        for uid in added:
            session.add(
                UserGroupMember(
                    group_id=group_id, user_id=uid, tenant_id=tenant_id, added_by=actor
                )
            )
        await session.commit()
    for uid in added + removed:
        invalidate_principal(uid)
    return {"added": added, "removed": removed}


async def list_user_groups(user_id: str, tenant_id: str = "default") -> List[GroupInfo]:
    """Groups a user belongs to, in membership order."""
    async with get_async_session_maker()() as session:
        gids = list(
            (
                await session.execute(
                    select(UserGroupMember.group_id)
                    .where(
                        UserGroupMember.user_id == user_id,
                        UserGroupMember.tenant_id == tenant_id,
                    )
                    .order_by(UserGroupMember.id)
                )
            ).scalars()
        )
    by_id = {g.id: g for g in await list_groups(tenant_id)}
    return [by_id[g] for g in gids if g in by_id]


async def set_user_groups(
    user_id: str,
    group_ids: Sequence[str],
    *,
    tenant_id: str = "default",
    actor: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Replace the groups of one user (the user-page counterpart of
    ``set_members``). Unknown group ids raise ``GroupNotFound``."""
    wanted = list(dict.fromkeys(g.strip() for g in group_ids if g and g.strip()))
    known = {g.id for g in await list_groups(tenant_id)}
    for gid in wanted:
        if gid not in known:
            raise GroupNotFound(gid)
    async with get_async_session_maker()() as session:
        current = set(
            (
                await session.execute(
                    select(UserGroupMember.group_id).where(
                        UserGroupMember.user_id == user_id,
                        UserGroupMember.tenant_id == tenant_id,
                    )
                )
            ).scalars()
        )
        added = [g for g in wanted if g not in current]
        removed = sorted(current - set(wanted))
        if removed:
            await session.execute(
                delete(UserGroupMember).where(
                    UserGroupMember.user_id == user_id,
                    UserGroupMember.group_id.in_(removed),
                )
            )
        for gid in added:
            session.add(
                UserGroupMember(
                    group_id=gid, user_id=user_id, tenant_id=tenant_id, added_by=actor
                )
            )
        await session.commit()
    invalidate_principal(user_id)
    return {"added": added, "removed": removed}


async def delete_user_access(user_id: str) -> None:
    """Remove a deleted user's memberships and direct rules (explicit cleanup:
    FK cascades are not enforced on patched SQLite databases)."""
    async with get_async_session_maker()() as session:
        await session.execute(
            delete(UserGroupMember).where(UserGroupMember.user_id == user_id)
        )
        await session.execute(
            delete(AccessGrant).where(
                AccessGrant.subject_type == SUBJECT_USER,
                AccessGrant.subject_id == user_id,
            )
        )
        await session.commit()
    invalidate_principal(user_id)
