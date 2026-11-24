"""Access decisions: the only place that answers "may this principal do X?".

Rules (``PLAN_access_control.md`` principle 3):

- ``profile``, ``skill``, ``mcp_server``, ``native_tool_group``,
  ``capability`` / ``use``: "no rule = no limit" per resource type; otherwise
  the union of the rules of the user and their groups (``Principal``). Admins
  follow the same rule (decision D1: an admin is unrestricted unless they
  restrict themself).
- ``skill`` / ``mcp_server`` / ``native_tool_group`` (Phase 2) govern only
  what a user may compose or turn on **by themself** (Integrations today,
  private profiles in Phase 3). They never strip anything from a profile the
  user may use: an admin-made profile works in full even if it contains a
  skill or MCP server the user has no rule for (product rule, see the plan).
  Rule ids may be glob patterns (``sql_*``), like profile skill lists.
- Private profiles (Phase 3, ``usr_*`` slugs) are usable only by their owner,
  whatever the ``profile`` rules say; nobody else can use them, admins
  included. ``GET /profiles`` lists them next to the allowed global ones.
- ``admin_section`` (Phase 5): four panel areas (``access``, ``agents``,
  ``external``, ``observability``) at ``read`` / ``write``, see
  ``src/access/admin_areas.py``. No rule = **no** area (unlike "use" rules);
  super-admins (``admin`` role) hold every area in write. ``resource_id``
  ``"*"`` / ``None`` = any area.

Unknown resource types are denied, so a typo never opens access.

Callers: ``src.api.profile_access`` (HTTP wrapper), ``src.main.get_agent``,
``GET /profiles``, Telegram/WhatsApp adapters, cron, ``require_permission``.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.access.admin_areas import (
    AREAS,
    CAPABILITY_ADMIN_PANEL,
    LEVEL_WRITE,
    combine,
    level_allows,
)
from src.access.principal import Principal

logger = logging.getLogger("aion.access.policy")

# Default profile of every client: a restricted user who cannot use it falls
# back to their first allowed profile instead of getting an error.
DEFAULT_PROFILE_SLUG = "aion_std"

# Resource types known to the policy (see the permission catalog in the plan).
RESOURCE_PROFILE = "profile"
RESOURCE_SKILL = "skill"
RESOURCE_MCP_SERVER = "mcp_server"
RESOURCE_NATIVE_TOOL_GROUP = "native_tool_group"
RESOURCE_CAPABILITY = "capability"
RESOURCE_ADMIN_SECTION = "admin_section"

# "use" resources governed by user/group rules (besides profiles).
_RULE_GOVERNED = frozenset(
    {
        RESOURCE_SKILL,
        RESOURCE_MCP_SERVER,
        RESOURCE_NATIVE_TOOL_GROUP,
        RESOURCE_CAPABILITY,
    }
)


class ProfileAccessDenied(Exception):
    """The principal may not use ``profile``; ``allowed`` lists what it may use.

    Transport-neutral on purpose: HTTP callers map it to 403
    ``profile_not_allowed``, the agent factory to ``ProfileNotFoundError``,
    channels to a chat message.
    """

    def __init__(self, profile: str, allowed: Sequence[str]):
        super().__init__(f"Profile '{profile}' is not allowed for this user")
        self.profile = profile
        self.allowed = list(allowed)


def _canonical_slug(profile_name: str) -> str:
    """Slug of a profile name/alias; the name itself if it does not resolve."""
    from src.agent_profile import ProfileNotFoundError, profile_manager

    try:
        return profile_manager.resolve_profile(profile_name).slug
    except ProfileNotFoundError:
        return profile_name


def _private_owner(slug: str) -> Optional[str]:
    """Owner (``users.id``) of a private profile, ``None`` for global ones."""
    from src.agent_profile import profile_manager

    return profile_manager.private_profile_owner(slug)


def can_use_profile(principal: Principal, profile_name: str) -> bool:
    """True if the principal may chat / schedule with ``profile_name``."""
    owner = _private_owner(_canonical_slug(profile_name))
    if owner is not None:
        return bool(principal.user_row_id) and owner == principal.user_row_id
    allowed = principal.allowed_profile_set
    if allowed is None:
        return True
    return _canonical_slug(profile_name) in allowed


def resolve_profile_for(principal: Principal, profile_name: str) -> str:
    """Profile to use for a request, applying the default-profile fallback.

    Returns ``profile_name`` unchanged when allowed (callers keep the name they
    sent), the first allowed slug when the default profile was requested but is
    not allowed, and raises ``ProfileAccessDenied`` otherwise.
    """
    slug = _canonical_slug(profile_name)
    owner = _private_owner(slug)
    if owner is not None:
        # Private profile: the owner always, anybody else never.
        if principal.user_row_id and owner == principal.user_row_id:
            return profile_name
        logger.warning(
            "user %s denied access to private profile %s", principal.user_row_id, slug
        )
        raise ProfileAccessDenied(profile_name, principal.profile_grants or [])
    grants = principal.profile_grants
    if grants is None:
        return profile_name
    if slug in grants:
        return profile_name
    # An empty tuple is a rule granting no profile at all: no fallback.
    if slug == DEFAULT_PROFILE_SLUG and grants:
        return grants[0]
    logger.warning("user %s denied access to profile %s", principal.user_row_id, slug)
    raise ProfileAccessDenied(profile_name, grants)


def filter_profiles(principal: Principal, profiles: Iterable[Any]) -> List[Any]:
    """Keep the profiles (dicts with ``slug`` or objects with ``.slug``) the
    principal may use. Used by profile listings (``GET /profiles``, channels)."""
    allowed = principal.allowed_profile_set
    items = list(profiles)
    if allowed is None:
        return items

    def _slug(p: Any) -> Optional[str]:
        return p.get("slug") if isinstance(p, dict) else getattr(p, "slug", None)

    return [p for p in items if _slug(p) in allowed]


def _matches(resource_id: str, allowed: Sequence[str]) -> bool:
    """True if ``resource_id`` is in ``allowed`` (exact or glob pattern)."""
    if resource_id in allowed:
        return True
    return any(
        ("*" in a or "?" in a) and fnmatch.fnmatchcase(resource_id, a) for a in allowed
    )


def filter_allowed(
    principal: Principal, resource_type: str, ids: Iterable[str]
) -> List[str]:
    """Keep the ``ids`` of ``resource_type`` the principal may use, in order.

    Used to narrow catalogues shown to the user (skills, MCP servers, native
    tool groups) and, from Phase 3, to drop revoked items from private
    profiles. Never applied to admin-made profiles (see module docstring).
    """
    items = list(ids)
    allowed = principal.allowed_ids(resource_type)
    if allowed is None:
        return items
    return [i for i in items if _matches(i, allowed)]


def allowed_skills(principal: Principal, names: Iterable[str]) -> List[str]:
    """Skills (of ``names``) the principal may add to their own profiles."""
    return filter_allowed(principal, RESOURCE_SKILL, names)


def allowed_mcp_servers(principal: Principal, slugs: Iterable[str]) -> List[str]:
    """MCP servers (of ``slugs``) the principal may turn on / add by themself."""
    return filter_allowed(principal, RESOURCE_MCP_SERVER, slugs)


def allowed_native_tool_groups(
    principal: Principal, groups: Iterable[str]
) -> List[str]:
    """Native tool groups (of ``groups``) the principal may add by themself."""
    return filter_allowed(principal, RESOURCE_NATIVE_TOOL_GROUP, groups)


def can(
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
) -> bool:
    """Generic permission check used by endpoints, tools and UIs.

    ``action`` is ``use`` / ``read`` / ``write``; ``resource_id`` is a slug,
    an admin section id or ``None``/``"*"`` for "any". Deny by default for
    unknown resource types.
    """
    if resource_type == RESOURCE_ADMIN_SECTION:
        areas = admin_areas(principal)
        if resource_id in (None, "*"):
            return any(level_allows(lv, action) for lv in areas.values())
        return level_allows(areas.get(resource_id), action)
    if resource_type == RESOURCE_PROFILE:
        if action != "use":
            return False
        if resource_id in (None, "*"):
            # "Can use at least one profile": unrestricted or non-empty grants.
            return principal.profile_grants is None or bool(principal.profile_grants)
        return can_use_profile(principal, resource_id)
    if resource_type in _RULE_GOVERNED:
        if action != "use":
            return False
        allowed = principal.allowed_ids(resource_type)
        if allowed is None:
            return True
        if resource_id in (None, "*"):
            return bool(allowed)
        return _matches(resource_id, allowed)
    logger.warning("can(): unknown resource type %r denied", resource_type)
    return False


def admin_areas(principal: Principal) -> Dict[str, str]:
    """Effective ``{area: "read"|"write"}`` of the admin panel.

    Super-admins get every area in write. Everybody else gets the sum of
    their own and their groups' ``admin_section`` rules (no rule = no area).
    API keys and internal calls never reach this with roles or grants of
    their owner (see ``require_admin_role``).
    """
    if principal.is_superadmin:
        return {a: LEVEL_WRITE for a in AREAS}
    return combine(principal.allowed_ids(RESOURCE_ADMIN_SECTION))


def can_enter_admin_panel(principal: Principal) -> bool:
    """True if the user may open the admin panel at all.

    Super-admins always; others when they hold at least one area **or** the
    base capability ``admin_panel.access`` is not excluded ("Solo chat UI").
    Areas granted by any rule win over "Solo chat UI" (permissions add up).
    Identities without a ``users`` row (anonymous) never enter.
    """
    if principal.is_superadmin:
        return True
    if not principal.exists:
        return False
    if admin_areas(principal):
        return True
    return can(principal, "use", RESOURCE_CAPABILITY, CAPABILITY_ADMIN_PANEL)


def effective_permissions(principal: Principal) -> List[str]:
    """Flat permission strings for clients (``/auth/me``).

    Format ``<resource_type>:<resource_id>:<action>``. Clients use them to
    show/hide UI; the backend always re-checks with ``can``.
    """
    perms: List[str] = []
    for rt in (RESOURCE_PROFILE, *sorted(_RULE_GOVERNED)):
        ids = principal.allowed_ids(rt)
        if ids is None:
            perms.append(f"{rt}:*:use")
        else:
            perms.extend(f"{rt}:{i}:use" for i in ids)
    # Admin areas: "write" implies "read", so both strings are listed.
    for area, level in sorted(admin_areas(principal).items()):
        perms.append(f"{RESOURCE_ADMIN_SECTION}:{area}:read")
        if level == LEVEL_WRITE:
            perms.append(f"{RESOURCE_ADMIN_SECTION}:{area}:write")
    return perms
