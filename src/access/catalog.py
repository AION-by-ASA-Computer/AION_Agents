"""Catalogue of the resources an admin can put in an access rule (Phase 2).

For each editable resource type it lists the ids that exist right now, with a
display name and a description:

- ``profile``: agent profiles (``profile_manager``),
- ``skill``: the skills of the registry that are not drafts
  (``skill_registry``): the same list as the admin-ui Skills page
  (``GET /admin/skills?status=verified``),
- ``mcp_server``: servers of the merged MCP registry (``mcp_manager``),
- ``native_tool_group``: bundles of the native tool registry,
- ``capability``: end-user features that can be switched off per user/group
  (``private_profiles.create``; the admin-ui translates the names).

Used by ``src/api/admin_groups.py`` to validate rules (so a typo cannot lock
users out or grant nothing by accident) and to feed the rule editors of the
admin-ui (``GET /admin/access/catalog``).
"""

from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger("aion.access.catalog")


def _profiles() -> List[Dict[str, str]]:
    from src.agent_profile import profile_manager

    return [
        {
            "id": str(p.get("slug")),
            "name": str(p.get("name") or p.get("slug")),
            "description": str(p.get("description") or ""),
        }
        for p in profile_manager.list_profiles()
        if p.get("slug")
    ]


def _skills() -> List[Dict[str, str]]:
    from src.skill_registry import skill_registry

    skill_registry.reload_if_stale()
    out: List[Dict[str, str]] = []
    for name in skill_registry.get_all_names():
        meta = skill_registry.get_meta(name) or {}
        # Drafts written by the skill distiller (data/skills/generated) are
        # not real skills until an admin promotes them: never offer them in
        # rules or private profiles.
        if meta.get("status") == "draft":
            continue
        out.append(
            {
                "id": name,
                "name": name,
                "description": str(meta.get("description") or ""),
            }
        )
    return out


def _mcp_servers() -> List[Dict[str, str]]:
    from src.mcp_manager import mcp_manager

    mcp_manager.load_registry()
    return [
        {
            "id": slug,
            "name": str((cfg or {}).get("display_name") or slug),
            "description": str((cfg or {}).get("description") or ""),
        }
        for slug, cfg in mcp_manager._registry.items()
    ]


def _native_tool_groups() -> List[Dict[str, str]]:
    from src.runtime.native_tools.registry_io import (
        load_merged_native_tool_registry,
    )

    bundles = (load_merged_native_tool_registry() or {}).get("bundles") or {}
    return [
        {
            "id": gid,
            "name": gid,
            "description": str((b or {}).get("description") or ""),
        }
        for gid, b in bundles.items()
    ]


# End-user features governed by ``capability`` rules (Phase 3+). Allowed to
# everyone while no rule restricts them (decision D2).
CAPABILITIES = (
    {
        "id": "private_profiles.create",
        "name": "Private profiles",
        "description": "Create personal agent profiles in chat-ui.",
    },
)


def _capabilities() -> List[Dict[str, str]]:
    return [dict(c) for c in CAPABILITIES]


_LOADERS = {
    "profile": _profiles,
    "skill": _skills,
    "mcp_server": _mcp_servers,
    "native_tool_group": _native_tool_groups,
    "capability": _capabilities,
}


def resource_catalog(resource_type: str) -> List[Dict[str, str]]:
    """``[{id, name, description}]`` sorted by name; ``[]`` for unknown types.

    A failing source (broken registry file) yields an empty list and a
    warning instead of breaking the whole admin page.
    """
    loader = _LOADERS.get(resource_type)
    if loader is None:
        return []
    try:
        items = loader()
    except Exception:
        logger.warning("catalog for %s unavailable", resource_type, exc_info=True)
        return []
    return sorted(items, key=lambda x: (x["name"].lower(), x["id"]))


def resource_ids(resource_type: str) -> List[str]:
    """Just the ids of ``resource_catalog(resource_type)``."""
    return [x["id"] for x in resource_catalog(resource_type)]
