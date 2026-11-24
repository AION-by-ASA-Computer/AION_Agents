"""The calling user as seen by the access layer (``Principal``).

A ``Principal`` bundles everything a permission decision needs: the user row,
its roles **read from the DB** (not from the login token, so promotions and
revocations apply without re-login), its groups and its effective "use"
access per resource type (direct rules + group rules, see ``src.access.store``).

Principals are cached in-process for a short TTL (``AION_ACCESS_CACHE_TTL_SEC``,
default 15s) because they are read on every chat request; every write that
changes roles or access must call ``invalidate_principal`` so changes apply
immediately. The backend runs with ``--workers 1`` (see CLAUDE.md), so an
in-process cache is coherent.

Used by ``src.access.policy``, ``src.api.auth_login`` (roles refresh) and the
``/auth/me`` payload.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger("aion.access.principal")

# Role that grants every ``admin_section`` permission (super-admin). Kept as a
# role on ``users.roles`` so the admin can never lock themself out via grants.
SUPERADMIN_ROLE = "admin"


def _tenant(tenant_id: Optional[str] = None) -> str:
    """Effective tenant: explicit value, else ``AION_DEFAULT_TENANT_ID``."""
    t = (tenant_id or os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip()
    return t or "default"


def _cache_ttl() -> float:
    """Principal cache TTL in seconds; ``0`` disables the cache."""
    try:
        return max(0.0, float(os.getenv("AION_ACCESS_CACHE_TTL_SEC", "15")))
    except ValueError:
        return 15.0


@dataclass(frozen=True)
class Principal:
    """Effective identity + access data of one user.

    Effective "use" access per resource type follows the rule model in
    ``src/access/store.py``: ``None`` = no limit (no subject of the user has a
    rule for that type, or a rule says ``*``), otherwise the ordered tuple of
    allowed ids (possibly empty).

    ``profile_grants`` is that value for profiles; the order matters (direct
    rules first, then groups in membership order) because the first slug is
    the fallback for the default profile. ``allowed`` holds the other types.
    ``exists`` is False for identities without a ``users`` row (anonymous /
    legacy ``default`` user): they are never restricted, as today.
    """

    user_row_id: Optional[str]
    identifier: Optional[str] = None
    tenant_id: str = "default"
    roles: Tuple[str, ...] = ()
    account_type: str = "human"
    exists: bool = False
    profile_grants: Optional[Tuple[str, ...]] = None
    # Slugs of the user's groups, in membership order.
    groups: Tuple[str, ...] = field(default_factory=tuple)
    # resource_type -> allowed ids (None / missing = no limit), except profiles.
    allowed: Dict[str, Optional[Tuple[str, ...]]] = field(
        default_factory=dict, compare=False, hash=False
    )

    @property
    def is_superadmin(self) -> bool:
        """True for users holding the ``admin`` role (full admin panel)."""
        return SUPERADMIN_ROLE in self.roles

    def allowed_ids(self, resource_type: str) -> Optional[Tuple[str, ...]]:
        """Allowed ids for a resource type (``None`` = no limit)."""
        if resource_type == "profile":
            return self.profile_grants
        return self.allowed.get(resource_type)

    @property
    def allowed_profile_set(self) -> Optional[FrozenSet[str]]:
        """Allowed profile slugs as a set, ``None`` = unrestricted."""
        if self.profile_grants is None:
            return None
        return frozenset(self.profile_grants)


# (engine id, tenant, user_row_id) -> (expires_at monotonic, Principal).
# The engine id keeps isolated test DBs (re-created engines) from sharing
# entries; in production there is a single engine.
_CACHE: Dict[Tuple[int, str, str], Tuple[float, Principal]] = {}


def _engine_key() -> int:
    """Identity of the current session factory, read WITHOUT side effects.

    ``get_engine()`` would lazily create an engine (and replace the session
    factory) when none exists, silently switching the DB under callers that
    installed their own factory; here we only look at the module globals.
    """
    from src.data import engine as engine_mod

    return id(getattr(engine_mod, "_session_factory", None))


def clear_principal_cache() -> None:
    """Drop every cached principal (tests, bulk changes)."""
    _CACHE.clear()


def invalidate_principal(user_row_id: Optional[str] = None) -> None:
    """Forget the cached principal of one user (all of them when ``None``).

    Call after any write that changes roles, profile access, group membership
    or grants, so the next request sees the new state.
    """
    if not user_row_id:
        _CACHE.clear()
        return
    for key in [k for k in _CACHE if k[2] == user_row_id]:
        _CACHE.pop(key, None)


async def resolve_user_row_id(
    user_id: Optional[str], tenant_id: Optional[str] = None
) -> Optional[str]:
    """``users.id`` from the identifier used in chat, or from the row id itself.

    Many tables store the identifier (``Mario_Rossi``) while access tables store
    the row id: this is the one helper that bridges them. ``"default"`` and
    empty values map to ``None`` (anonymous / single-user mode). Case-insensitive
    fallback on the identifier, without LIKE wildcards.
    """
    key = (user_id or "").strip()
    if not key or key == "default":
        return None
    from sqlalchemy import func, or_, select

    from src.data.engine import get_async_session_maker
    from src.data.models import User

    tenant = _tenant(tenant_id)
    async with get_async_session_maker()() as session:
        for cond in (
            or_(User.identifier == key, User.id == key),
            func.lower(User.identifier) == key.lower(),
        ):
            row = (
                await session.execute(
                    select(User.id).where(User.tenant_id == tenant, cond).limit(1)
                )
            ).first()
            if row:
                return row[0]
    return None


# Must match ``src.access.store`` (not imported: store imports this module).
_WILDCARD = "*"
_NONE_MARKER = "!none"


def _effective(rules: List[List[str]]) -> Optional[Tuple[str, ...]]:
    """Combine the rules of the subjects that have one for a resource type.

    No rule at all -> ``None`` (no limit). Otherwise the ordered union of the
    ids; a ``*`` anywhere -> ``None`` (everything). ``!none`` markers only
    signal that the rule exists and add nothing.
    """
    if not rules:
        return None
    out: List[str] = []
    for rule in rules:
        for rid in rule:
            if rid == _WILDCARD:
                return None
            if rid != _NONE_MARKER and rid not in out:
                out.append(rid)
    return tuple(out)


async def _load_from_db(user_row_id: str, tenant: str) -> Principal:
    """Read the user row, groups and "use" rules (one session, few small queries).

    Rule sources, in fallback order: the user's direct rules (``access_grants``
    plus legacy ``user_profile_access`` rows, read until that table is dropped),
    then each group in membership order.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from src.data.engine import get_async_session_maker
    from src.data.models import (
        AccessGrant,
        User,
        UserGroup,
        UserGroupMember,
        UserProfileAccess,
    )
    from src.data.user_password import get_roles

    async with get_async_session_maker()() as session:
        user = await session.get(User, user_row_id)
        if user is None:
            # No row: keep the legacy behaviour (no restriction, no DB roles).
            return Principal(user_row_id=user_row_id, tenant_id=tenant, exists=False)
        user_tenant = user.tenant_id or tenant
        # Snapshot the row now: a rollback below (missing tables) expires it.
        user_fields = {
            "user_row_id": user.id,
            "identifier": user.identifier,
            "roles": tuple(get_roles(user)),
            "account_type": getattr(user, "account_type", None) or "human",
        }

        legacy_slugs = list(
            (
                await session.execute(
                    select(UserProfileAccess.profile_slug)
                    .where(UserProfileAccess.user_id == user_row_id)
                    .order_by(UserProfileAccess.id)
                )
            ).scalars()
        )
        # The group tables exist once the backend has started (create_all +
        # Alembic j8g9m0e038). On a database not migrated yet (e.g. tests or
        # scripts run before the first start) there can be no groups or rules:
        # fall back to the legacy allowlist instead of failing every request.
        try:
            group_rows = (
                await session.execute(
                    select(UserGroup.id, UserGroup.slug)
                    .join(UserGroupMember, UserGroupMember.group_id == UserGroup.id)
                    .where(
                        UserGroupMember.user_id == user_row_id,
                        UserGroup.tenant_id == user_tenant,
                    )
                    .order_by(UserGroupMember.id)
                )
            ).all()
            group_ids = [g[0] for g in group_rows]
            grant_rows = (
                (
                    await session.execute(
                        select(AccessGrant)
                        .where(
                            AccessGrant.tenant_id == user_tenant,
                            AccessGrant.action == "use",
                            (
                                (AccessGrant.subject_type == "user")
                                & (AccessGrant.subject_id == user_row_id)
                            )
                            | (
                                (AccessGrant.subject_type == "group")
                                & (AccessGrant.subject_id.in_(group_ids or [""]))
                            ),
                        )
                        .order_by(AccessGrant.id)
                    )
                )
                .scalars()
                .all()
            )
        except (OperationalError, ProgrammingError) as exc:
            await session.rollback()
            logger.warning("access tables unavailable, using legacy rules: %s", exc)
            group_rows, grant_rows = [], []
        group_ids = [g[0] for g in group_rows]
        subjects = [("user", user_row_id)] + [("group", gid) for gid in group_ids]

    # subject -> resource_type -> ids (presence of the key = "has a rule").
    by_subject: Dict[Tuple[str, str], Dict[str, List[str]]] = {s: {} for s in subjects}
    for g in grant_rows:
        rules = by_subject.get((g.subject_type, g.subject_id))
        if rules is not None:
            rules.setdefault(g.resource_type, []).append(g.resource_id)
    if legacy_slugs:
        direct_profiles = by_subject[("user", user_row_id)].setdefault("profile", [])
        direct_profiles.extend(s for s in legacy_slugs if s not in direct_profiles)

    resource_types = {rt for rules in by_subject.values() for rt in rules}
    allowed = {
        rt: _effective([by_subject[s][rt] for s in subjects if rt in by_subject[s]])
        for rt in resource_types
    }
    return Principal(
        **user_fields,
        tenant_id=user_tenant,
        exists=True,
        profile_grants=allowed.pop("profile", None),
        groups=tuple(g[1] for g in group_rows),
        allowed=allowed,
    )


async def load_principal(
    user_row_id: Optional[str], tenant_id: Optional[str] = None
) -> Principal:
    """Principal for a ``users.id`` (cached). ``None`` -> anonymous principal.

    Callers holding only an identifier should use ``load_principal_for``.
    """
    tenant = _tenant(tenant_id)
    if not user_row_id:
        return Principal(user_row_id=None, tenant_id=tenant)
    ttl = _cache_ttl()
    key = (_engine_key(), tenant, user_row_id)
    now = time.monotonic()
    if ttl > 0:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    principal = await _load_from_db(user_row_id, tenant)
    if ttl > 0:
        _CACHE[key] = (now + ttl, principal)
    return principal


async def load_principal_for(
    user_id: Optional[str], tenant_id: Optional[str] = None
) -> Principal:
    """Principal from an identifier *or* a row id (chat, cron, channels)."""
    row_id = await resolve_user_row_id(user_id, tenant_id)
    return await load_principal(row_id, tenant_id)
