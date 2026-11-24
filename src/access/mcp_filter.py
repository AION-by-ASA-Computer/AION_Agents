"""Per-user view of a profile's MCP servers, applied once at agent build.

A user can switch off an integration in chat-ui (``user_mcp_preferences``)
when the admin allows it (``mcp_server_configs.user_may_disable``). Before this
module the switch was honoured only for *resident* servers: deferred servers
(loaded on demand via ``tool_search`` / ``activate_tool_group``) and the
"Tools on demand" prompt manifest ignored it.

``effective_profile_for_user`` returns a shallow copy of the profile without
the disabled servers, so every downstream consumer (resident discovery,
deferred groups, prompt manifest, agent cache key via ``profile_sig``) sees the
same filtered list. The shared profile object from ``profile_manager`` is never
mutated.

Called by ``src.main.get_agent`` right after the profile is resolved.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Iterable, Optional, Set

logger = logging.getLogger("aion.access.mcp_filter")


async def user_disabled_mcp_servers(
    user_id: Optional[str],
    server_slugs: Iterable[str],
    tenant_id: str = "default",
) -> Set[str]:
    """Slugs among ``server_slugs`` the user switched off (and may switch off).

    Best effort: on DB errors nothing is filtered (same as the previous inline
    logic in ``build_all_tools``), so a broken preferences table never removes
    tools from every agent.
    """
    slugs = [s for s in server_slugs if s]
    if not slugs or not user_id or user_id == "default":
        return set()
    try:
        from sqlalchemy import select

        from src.data.engine import get_async_session_maker
        from src.data.models import McpServerConfig
        from src.runtime.mcp_integration_helpers import (
            get_user_mcp_preference_map,
            user_mcp_effective_active,
        )

        pref_map = await get_user_mcp_preference_map(user_id, tenant_id=tenant_id)
        if not pref_map:
            return set()
        async with get_async_session_maker()() as session:
            rows = (
                (
                    await session.execute(
                        select(McpServerConfig).where(
                            McpServerConfig.server_slug.in_(slugs)
                        )
                    )
                )
                .scalars()
                .all()
            )
        policy_by_slug = {r.server_slug: r for r in rows}
        disabled: Set[str] = set()
        for slug in slugs:
            pol = policy_by_slug.get(slug)
            # Servers without a config row cannot be switched off by users.
            if pol and not user_mcp_effective_active(
                slug,
                pref_map=pref_map,
                user_may_disable=bool(getattr(pol, "user_may_disable", True)),
            ):
                disabled.add(slug)
        return disabled
    except Exception as ex:
        logger.debug("MCP user preference filter skipped: %s", ex)
        return set()


async def effective_profile_for_user(
    profile: Any, user_id: Optional[str], tenant_id: str = "default"
) -> Any:
    """``profile`` itself, or a copy without the servers the user disabled."""
    servers = list(getattr(profile, "mcp_servers", None) or [])
    disabled = await user_disabled_mcp_servers(user_id, servers, tenant_id)
    if not disabled:
        return profile
    filtered = copy.copy(profile)
    filtered.mcp_servers = [s for s in servers if s not in disabled]
    logger.debug(
        "profile %s: MCP servers disabled by user %s: %s",
        getattr(profile, "slug", "?"),
        user_id,
        sorted(disabled),
    )
    return filtered


async def configurable_mcp_servers(principal: Any) -> Optional[Set[str]]:
    """MCP servers the user may see and set up in Integrations (Phase 2).

    ``None`` = no limit (no ``mcp_server`` rule for the user or their groups).
    Otherwise the servers their rules allow **plus** the servers used by the
    profiles they may use: without the latter a user could not enter the
    credentials of an MCP server needed by a profile an admin assigned them
    (product rule: profile permissions win over MCP permissions).

    Glob patterns in rules are expanded against the MCP registry. Called by
    ``src/api/v1/mcp_integrations.py`` (list, activation, credentials, OAuth).
    """
    from src.access.policy import allowed_mcp_servers, filter_profiles
    from src.agent_profile import profile_manager
    from src.mcp_manager import mcp_manager

    if principal.allowed_ids("mcp_server") is None:
        return None
    mcp_manager.load_registry()
    out: Set[str] = set(allowed_mcp_servers(principal, mcp_manager._registry.keys()))
    # Servers of usable profiles stay configurable even without an MCP rule.
    for p in filter_profiles(principal, profile_manager.list_profiles()):
        out.update(str(s) for s in (p.get("mcp_servers") or []) if s)
    return out
