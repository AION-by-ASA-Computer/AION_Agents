"""Access control: the single decision point for "who can use / manage what".

Every endpoint, channel (Telegram, WhatsApp), cron path and agent build asks
this package instead of querying the access tables directly, so the rules live
in one place and can grow (groups, grants, admin RBAC) without touching the
call sites. See ``PLAN_access_control.md`` for the roadmap.

- ``principal``: who is calling (user row, roles, profile grants) with a short
  in-process cache and explicit invalidation.
- ``policy``: the decisions (``can``, profile resolution/filtering).
- ``mcp_filter``: per-user view of a profile's MCP servers for the agent build.
"""

from src.access.policy import (
    ProfileAccessDenied,
    can,
    can_use_profile,
    filter_profiles,
    resolve_profile_for,
)
from src.access.principal import (
    Principal,
    clear_principal_cache,
    invalidate_principal,
    load_principal,
    resolve_user_row_id,
)

__all__ = [
    "Principal",
    "ProfileAccessDenied",
    "can",
    "can_use_profile",
    "clear_principal_cache",
    "filter_profiles",
    "invalidate_principal",
    "load_principal",
    "resolve_profile_for",
    "resolve_user_row_id",
]
