"""What the calling user may use: ``GET /v1/me/access`` (access control, Phase 2).

Clients (chat-ui today, the private-profile editor and the ``aion_self`` tools
later) read it to show only the profiles, skills, MCP servers and native tool
groups the user may pick. It is a view for the UI only: every endpoint checks
again through ``src.access``.

For each resource type it returns ``limited`` (``False`` = no rule anywhere,
everything allowed) and ``allowed`` (ids that exist right now, glob rules
expanded; ``null`` = everything). ``mcp_server`` also includes the servers of
the user's profiles under ``configurable`` (see ``configurable_mcp_servers``).
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends

from src.access.catalog import resource_ids
from src.access.mcp_filter import configurable_mcp_servers
from src.access.policy import filter_allowed
from src.access.principal import load_principal_for
from src.access.store import EDITABLE_RESOURCE_TYPES
from src.api.auth_login import ChatAuthIdentity, require_chat_auth

router = APIRouter(prefix="/me/access", tags=["v1-me-access"])


@router.get("")
async def get_my_access(
    auth: ChatAuthIdentity = Depends(require_chat_auth),
) -> Dict[str, Any]:
    """Effective access of the caller (API keys act as their owner)."""
    tenant = (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip() or "default"
    principal = await load_principal_for(auth.user_row_id or auth.identifier, tenant)
    out: Dict[str, Any] = {}
    for rt in EDITABLE_RESOURCE_TYPES:
        limited = principal.allowed_ids(rt) is not None
        out[rt] = {
            "limited": limited,
            "allowed": filter_allowed(principal, rt, resource_ids(rt))
            if limited
            else None,
        }
    scope = await configurable_mcp_servers(principal)
    out["mcp_server"]["configurable"] = sorted(scope) if scope is not None else None
    return {"groups": list(principal.groups), "access": out}
