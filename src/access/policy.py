"""Access decisions: the only place that answers "may this principal do X?".

Rules (``PLAN_access_control.md`` principle 3):

- ``profile``, ``skill``, ``mcp_server``, ``native_tool_group``,
  ``capability`` / ``use``: "no rule = no limit" per resource type; otherwise
  the union of the rules of the user and their groups (``Principal``). Admins
  follow the same rule (decision D1: an admin is unrestricted unless they
  restrict themself). Phase 1 only lets admins set ``profile`` rules; the other
  types get their UI and enforcement points in Phases 2-3.
- ``admin_section`` / any action: super-admin only (``admin`` role) until
  Phase 5.

Unknown resource types are denied, so a typo never opens access.

Callers: ``src.api.profile_access`` (HTTP wrapper), ``src.main.get_agent``,
``GET /profiles``, Telegram/WhatsApp adapters, cron, ``require_permission``.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional, Sequence

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


def can_use_profile(principal: Principal, profile_name: str) -> bool:
    """True if the principal may chat / schedule with ``profile_name``."""
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
    grants = principal.profile_grants
    if grants is None:
        return profile_name
    slug = _canonical_slug(profile_name)
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
        return principal.is_superadmin
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
        return resource_id in allowed
    logger.warning("can(): unknown resource type %r denied", resource_type)
    return False


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
    if principal.is_superadmin:
        perms.append(f"{RESOURCE_ADMIN_SECTION}:*:read")
        perms.append(f"{RESOURCE_ADMIN_SECTION}:*:write")
    return perms
