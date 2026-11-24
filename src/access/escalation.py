"""Anti-escalation rules of the admin panel (access control, Phase 5).

Anybody with ``access:write`` may manage users, groups and rules and may give
**any** permission to anyone, with these exceptions for non-super-admins
(``PLAN_access_control.md`` §5.2):

- ``self``               never on their own account, rules or groups;
- ``own_group``          never on a group they belong to (otherwise they could
                         grant themselves permissions through the group), and
                         they cannot join a group;
- ``superadmin_target``  never on a super-admin (account, rules, groups,
                         password, 2FA, SSO identities), never on a group
                         containing a super-admin;
- ``admin_role_reserved`` only super-admins give or remove the ``admin`` role
                         (users are created as normal users).

Super-admins (and the ``AION_ADMIN_PASSWORD_AUTH=0`` dev escape hatch) are
never limited. Endpoints call these guards *after* the route permission
check of ``require_admin_role``; a refusal is ``403`` with
``{"code": "permission_denied", "reason": ...}``.

Used by ``src/api/admin.py`` (users), ``src/api/admin_groups.py`` and
``src/api/admin_auth_mode.py``.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import select

from src.access.principal import SUPERADMIN_ROLE
from src.data.engine import get_async_session_maker
from src.data.models import User, UserGroupMember

REASON_SELF = "self"
REASON_OWN_GROUP = "own_group"
REASON_SUPERADMIN = "superadmin_target"
REASON_ADMIN_ROLE = "admin_role_reserved"


def _deny(reason: str) -> HTTPException:
    return HTTPException(
        403,
        detail={
            "code": "permission_denied",
            "reason": reason,
            "message": "This change is reserved to super-admins",
        },
    )


def _is_unrestricted(auth) -> bool:
    """Super-admin caller, or admin auth disabled (dev escape hatch)."""
    from src.api.auth_login import admin_password_auth_enabled

    if not admin_password_auth_enabled():
        return True
    return SUPERADMIN_ROLE in (auth.roles or [])


async def superadmin_ids(user_ids: Iterable[str]) -> Set[str]:
    """Those of ``user_ids`` that hold the ``admin`` role (read from the DB)."""
    from src.data.user_password import get_roles

    ids = [u for u in user_ids if u]
    if not ids:
        return set()
    async with get_async_session_maker()() as session:
        rows = (
            (await session.execute(select(User).where(User.id.in_(ids))))
            .scalars()
            .all()
        )
    return {u.id for u in rows if SUPERADMIN_ROLE in get_roles(u)}


async def _group_member_ids(group_id: str) -> List[str]:
    async with get_async_session_maker()() as session:
        return list(
            (
                await session.execute(
                    select(UserGroupMember.user_id).where(
                        UserGroupMember.group_id == group_id
                    )
                )
            ).scalars()
        )


async def ensure_can_manage_user(auth, target_user_id: str) -> None:
    """Refuse changes to the caller's own account or to a super-admin."""
    if _is_unrestricted(auth):
        return
    if target_user_id and target_user_id == auth.user_row_id:
        raise _deny(REASON_SELF)
    if await superadmin_ids([target_user_id]):
        raise _deny(REASON_SUPERADMIN)


def ensure_can_set_roles(
    auth, roles: Optional[Iterable[str]], current: Iterable[str] = ()
) -> None:
    """Only super-admins add or remove the ``admin`` role (create admins)."""
    if roles is None or _is_unrestricted(auth):
        return
    if (SUPERADMIN_ROLE in set(roles)) != (SUPERADMIN_ROLE in set(current)):
        raise _deny(REASON_ADMIN_ROLE)


async def ensure_can_manage_group(auth, group_id: str) -> None:
    """Refuse changes to a group the caller belongs to or that has a super-admin."""
    if _is_unrestricted(auth):
        return
    members = await _group_member_ids(group_id)
    if auth.user_row_id in members:
        raise _deny(REASON_OWN_GROUP)
    if await superadmin_ids(members):
        raise _deny(REASON_SUPERADMIN)


async def ensure_can_set_members(
    auth, group_id: Optional[str], new_member_ids: Iterable[str]
) -> None:
    """Membership changes: the group must be manageable and the new member
    list must not add the caller or a super-admin."""
    if _is_unrestricted(auth):
        return
    if group_id:
        await ensure_can_manage_group(auth, group_id)
    new_ids = set(new_member_ids)
    if auth.user_row_id in new_ids:
        raise _deny(REASON_OWN_GROUP)
    if await superadmin_ids(new_ids):
        raise _deny(REASON_SUPERADMIN)


async def ensure_can_set_user_groups(
    auth, user_id: str, group_ids: Iterable[str]
) -> None:
    """Changing a user's groups: the user must be manageable and every group
    added or removed must be manageable too (no group with a super-admin)."""
    if _is_unrestricted(auth):
        return
    await ensure_can_manage_user(auth, user_id)
    from src.access import store

    import os

    tenant = (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip() or "default"
    current = {g.id for g in await store.list_user_groups(user_id, tenant)}
    for gid in current.symmetric_difference(set(group_ids)):
        await ensure_can_manage_group(auth, gid)
