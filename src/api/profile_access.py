"""HTTP-facing helpers for per-user profile access.

Thin wrappers over ``src.access`` (the single decision point) kept for the
existing call sites: chat endpoints, conversations, cron, a2a, Telegram and
WhatsApp. They work the same for chat tokens and API keys (a key acts as its
owner). New code should use ``src.access`` directly.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import HTTPException

from src.access.policy import (
    DEFAULT_PROFILE_SLUG as _DEFAULT_PROFILE_SLUG,  # noqa: F401 (compat export)
)
from src.access.policy import ProfileAccessDenied, resolve_profile_for
from src.access.principal import load_principal, resolve_user_row_id


async def allowed_profile_slugs(user_row_id: Optional[str]) -> List[str]:
    """Allowed slugs for ``users.id``; empty list = no restriction (legacy shape).

    Deprecated: it cannot express a rule granting *no* profile (also ``[]``).
    Use ``load_principal(...).profile_grants`` (``None`` = no limit) instead.
    """
    principal = await load_principal(user_row_id)
    return list(principal.profile_grants or [])


async def user_row_id_for(
    user_id: Optional[str], tenant_id: str = "default"
) -> Optional[str]:
    """``users.id`` from the chat identifier (or the id itself); see
    ``src.access.principal.resolve_user_row_id``."""
    return await resolve_user_row_id(user_id, tenant_id)


async def enforce_profile_access(user_row_id: Optional[str], profile_name: str) -> str:
    """Profile to use for the request; 403 ``profile_not_allowed`` if denied."""
    principal = await load_principal(user_row_id)
    try:
        return resolve_profile_for(principal, profile_name)
    except ProfileAccessDenied:
        raise HTTPException(
            403,
            detail={
                "code": "profile_not_allowed",
                "message": f"Profile '{profile_name}' is not allowed for this user",
            },
        ) from None
