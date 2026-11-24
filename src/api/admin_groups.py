"""Admin API for user groups and access rules (access control, Phases 1-2).

Endpoints (all super-admin only, like every ``/admin/*`` route):

- ``/admin/groups``: list / create; ``/admin/groups/{id}``: read / update /
  delete; ``/admin/groups/{id}/members``: replace members;
  ``/admin/groups/{id}/rules``: read / replace the group's rules.
- ``/admin/users/{id}/groups``: read / replace a user's groups;
  ``/admin/users/{id}/rules``: read / replace the user's direct rules;
  ``/admin/users/{id}/effective-access``: what the user can use and *why*.
- ``/admin/access/catalog``: ids that can go in a rule, per resource type.

Rules are ``{resource_type: [ids] | null}`` (``null`` = no rule, ``[]`` =
nothing, ``["*"]`` = everything); semantics in ``src/access/store.py`` and
``PLAN_access_control.md``. Editable types: ``profile`` (Phase 1),
``skill``, ``mcp_server``, ``native_tool_group`` (Phase 2).

Writes are refused by ``src.access.escalation`` when a non-super-admin
targets themself, a super-admin or a group containing either (Phase 5).

Persistence (and principal cache invalidation) is in ``src.access.store``;
this module validates input, maps errors to HTTP and writes the audit log.
Used by the admin-ui Users page (tabs "Account" and "Gruppi").
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.access import escalation, store
from src.access.catalog import resource_catalog, resource_ids
from src.access.principal import load_principal
from src.api.auth_login import ChatAuthIdentity, require_admin_role
from src.data.audit import record_admin_action
from src.data.engine import get_async_session_maker
from src.data.models import User

logger = logging.getLogger("aion.api.admin_groups")

router = APIRouter(
    prefix="/admin",
    tags=["admin_groups"],
    dependencies=[Depends(require_admin_role)],
)


def _tenant_id() -> str:
    return (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip() or "default"


# --- request models ----------------------------------------------------------


class GroupCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)


class GroupUpdate(BaseModel):
    slug: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=2000)


class MembersUpdate(BaseModel):
    user_ids: List[str] = Field(default_factory=list, max_length=10000)


class UserGroupsUpdate(BaseModel):
    group_ids: List[str] = Field(default_factory=list, max_length=500)


class RulesUpdate(BaseModel):
    """Only the resource types present are changed; ``null`` removes a rule."""

    rules: Dict[str, Optional[List[str]]] = Field(default_factory=dict)


# --- helpers -----------------------------------------------------------------


def _group_out(g: store.GroupInfo) -> Dict[str, Any]:
    return {
        "id": g.id,
        "slug": g.slug,
        "name": g.name,
        "description": g.description,
        "member_count": g.member_count,
    }


def _validate_rules(
    rules: Dict[str, Optional[List[str]]],
) -> Dict[str, Optional[List[str]]]:
    """422 on resource types not editable yet or ids that do not exist.

    ``*`` is always accepted, and so are glob patterns (``sql_*``) for
    skills, MCP servers and native tool groups as long as they match at
    least one existing id. Ids are checked against ``src.access.catalog`` so a
    typo cannot silently lock users out. Unknown profiles keep the Phase 1
    error code ``unknown_profiles``; other types return ``unknown_resources``.

    ``admin_section`` (Phase 5) accepts only exact ``<area>:<level>`` ids (no
    ``*``, no patterns: the full panel is the super-admin role); when both
    levels of an area are sent, only ``write`` is kept (it includes read).
    """
    import fnmatch

    out: Dict[str, Optional[List[str]]] = {}
    for rt, entries in rules.items():
        if rt not in store.EDITABLE_RESOURCE_TYPES:
            raise HTTPException(
                422,
                detail={"code": "resource_type_not_editable", "resource_type": rt},
            )
        normalized = store.normalize_rule(entries)
        exact_only = rt in ("profile", "admin_section", "capability")
        if rt == "admin_section" and normalized == [store.WILDCARD]:
            raise HTTPException(
                422,
                detail={"code": "unknown_resources", "resource_type": rt, "ids": ["*"]},
            )
        if normalized and normalized != [store.WILDCARD]:
            known = set(resource_ids(rt))
            globs_ok = not exact_only
            unknown = [
                i
                for i in normalized
                if i not in known
                and not (
                    globs_ok and ("*" in i or "?" in i) and fnmatch.filter(known, i)
                )
            ]
            if unknown:
                if rt == "profile":
                    raise HTTPException(
                        422, detail={"code": "unknown_profiles", "profiles": unknown}
                    )
                raise HTTPException(
                    422,
                    detail={
                        "code": "unknown_resources",
                        "resource_type": rt,
                        "ids": unknown,
                    },
                )
        if rt == "admin_section" and normalized:
            write_areas = {i.split(":")[0] for i in normalized if i.endswith(":write")}
            normalized = [
                i
                for i in normalized
                if not (i.endswith(":read") and i.split(":")[0] in write_areas)
            ]
        out[rt] = normalized
    return out


async def _get_user_or_404(user_id: str) -> User:
    async with get_async_session_maker()() as session:
        u = await session.get(User, user_id)
    if u is None or (u.tenant_id or "default") != _tenant_id():
        raise HTTPException(404, detail={"code": "user_not_found"})
    return u


async def _group_or_404(group_id: str) -> store.GroupInfo:
    try:
        return await store.get_group(group_id, _tenant_id())
    except store.GroupNotFound:
        raise HTTPException(404, detail={"code": "group_not_found"}) from None


async def _audit(
    auth: ChatAuthIdentity,
    operation: str,
    rtype: str,
    rid: str,
    payload: Dict[str, Any],
) -> None:
    await record_admin_action(
        tenant_id=_tenant_id(),
        actor=auth.identifier,
        operation=operation,
        resource_type=rtype,
        resource_id=rid,
        payload=payload,
    )


# --- catalogue ---------------------------------------------------------------


@router.get("/access/catalog")
async def get_access_catalog() -> Dict[str, Any]:
    """Ids that can go in a rule, per editable resource type.

    ``{"types": {"profile": [{id, name, description}], "skill": [...], ...}}``,
    used by the rule editors of the admin-ui.
    """
    return {"types": {rt: resource_catalog(rt) for rt in store.EDITABLE_RESOURCE_TYPES}}


# --- groups ------------------------------------------------------------------


@router.get("/groups")
async def list_groups() -> Dict[str, Any]:
    """All groups with member counts, sorted by name."""
    return {"items": [_group_out(g) for g in await store.list_groups(_tenant_id())]}


@router.post("/groups", status_code=201)
async def create_group(
    body: GroupCreate, auth: ChatAuthIdentity = Depends(require_admin_role)
) -> Dict[str, Any]:
    """Create an empty group (no members, no rules -> no effect yet)."""
    try:
        g = await store.create_group(
            slug=body.slug,
            name=body.name,
            description=body.description,
            tenant_id=_tenant_id(),
            actor=auth.identifier,
        )
    except store.InvalidSlug:
        raise HTTPException(422, detail={"code": "invalid_group_slug"}) from None
    except store.GroupSlugTaken:
        raise HTTPException(409, detail={"code": "group_slug_taken"}) from None
    await _audit(auth, "group.create", "group", g.id, {"slug": g.slug, "name": g.name})
    return _group_out(g)


@router.get("/groups/{group_id}")
async def get_group(group_id: str) -> Dict[str, Any]:
    """Group details with members (id, identifier, display name) and rules."""
    g = await _group_or_404(group_id)
    member_ids = await store.list_member_ids(group_id)
    members: List[Dict[str, Any]] = []
    if member_ids:
        async with get_async_session_maker()() as session:
            rows = (
                (await session.execute(select(User).where(User.id.in_(member_ids))))
                .scalars()
                .all()
            )
        by_id = {u.id: u for u in rows}
        members = [
            {
                "id": uid,
                "identifier": by_id[uid].identifier,
                "display_name": by_id[uid].display_name,
                "account_type": by_id[uid].account_type,
            }
            for uid in member_ids
            if uid in by_id
        ]
    rules = await store.get_rules(store.SUBJECT_GROUP, group_id, tenant_id=_tenant_id())
    return {**_group_out(g), "members": members, "rules": rules}


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: str,
    body: GroupUpdate,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Rename / re-describe a group."""
    await escalation.ensure_can_manage_group(auth, group_id)
    try:
        g = await store.update_group(
            group_id,
            slug=body.slug,
            name=body.name,
            description=body.description,
            tenant_id=_tenant_id(),
        )
    except store.GroupNotFound:
        raise HTTPException(404, detail={"code": "group_not_found"}) from None
    except store.InvalidSlug:
        raise HTTPException(422, detail={"code": "invalid_group_slug"}) from None
    except store.GroupSlugTaken:
        raise HTTPException(409, detail={"code": "group_slug_taken"}) from None
    await _audit(
        auth, "group.update", "group", g.id, body.model_dump(exclude_none=True)
    )
    return _group_out(g)


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: str, auth: ChatAuthIdentity = Depends(require_admin_role)
) -> Dict[str, Any]:
    """Delete a group: its members lose the group's rules immediately."""
    await escalation.ensure_can_manage_group(auth, group_id)
    g = await _group_or_404(group_id)
    await store.delete_group(group_id, _tenant_id())
    await _audit(auth, "group.delete", "group", group_id, {"slug": g.slug})
    return {"status": "success"}


@router.put("/groups/{group_id}/members")
async def set_group_members(
    group_id: str,
    body: MembersUpdate,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Replace the members of a group (``users.id`` list)."""
    await escalation.ensure_can_set_members(auth, group_id, body.user_ids)
    try:
        diff = await store.set_members(
            group_id, body.user_ids, tenant_id=_tenant_id(), actor=auth.identifier
        )
    except store.GroupNotFound:
        raise HTTPException(404, detail={"code": "group_not_found"}) from None
    except ValueError as e:
        raise HTTPException(
            422, detail={"code": "unknown_users", "message": str(e)}
        ) from None
    await _audit(auth, "group.members.update", "group", group_id, diff)
    return {"status": "success", **diff}


@router.get("/groups/{group_id}/rules")
async def get_group_rules(group_id: str) -> Dict[str, Any]:
    """Rules of a group (types without a rule are absent)."""
    await _group_or_404(group_id)
    rules = await store.get_rules(store.SUBJECT_GROUP, group_id, tenant_id=_tenant_id())
    return {"rules": rules}


@router.put("/groups/{group_id}/rules")
async def set_group_rules(
    group_id: str,
    body: RulesUpdate,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Replace the group's rules for the resource types in the body."""
    await escalation.ensure_can_manage_group(auth, group_id)
    await _group_or_404(group_id)
    rules = _validate_rules(body.rules)
    await store.set_rules(
        store.SUBJECT_GROUP,
        group_id,
        rules,
        tenant_id=_tenant_id(),
        actor=auth.identifier,
    )
    await _audit(auth, "group.rules.update", "group", group_id, {"rules": rules})
    return {
        "status": "success",
        "rules": await store.get_rules(
            store.SUBJECT_GROUP, group_id, tenant_id=_tenant_id()
        ),
    }


# --- users -------------------------------------------------------------------


@router.get("/users/{user_id}/groups")
async def get_user_groups(user_id: str) -> Dict[str, Any]:
    """Groups of a user, in membership order."""
    await _get_user_or_404(user_id)
    groups = await store.list_user_groups(user_id, _tenant_id())
    return {"items": [_group_out(g) for g in groups]}


@router.put("/users/{user_id}/groups")
async def set_user_groups(
    user_id: str,
    body: UserGroupsUpdate,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Replace the groups of a user."""
    await escalation.ensure_can_set_user_groups(auth, user_id, body.group_ids)
    await _get_user_or_404(user_id)
    try:
        diff = await store.set_user_groups(
            user_id, body.group_ids, tenant_id=_tenant_id(), actor=auth.identifier
        )
    except store.GroupNotFound as e:
        raise HTTPException(
            422, detail={"code": "group_not_found", "group_id": str(e)}
        ) from None
    await _audit(auth, "user.groups.update", "user", user_id, diff)
    return {"status": "success", **diff}


@router.get("/users/{user_id}/rules")
async def get_user_rules(user_id: str) -> Dict[str, Any]:
    """Direct rules of a user (legacy profile allowlist included)."""
    await _get_user_or_404(user_id)
    rules = await store.get_rules(store.SUBJECT_USER, user_id, tenant_id=_tenant_id())
    return {"rules": rules}


@router.put("/users/{user_id}/rules")
async def set_user_rules(
    user_id: str,
    body: RulesUpdate,
    auth: ChatAuthIdentity = Depends(require_admin_role),
) -> Dict[str, Any]:
    """Replace the user's direct rules for the resource types in the body."""
    await escalation.ensure_can_manage_user(auth, user_id)
    await _get_user_or_404(user_id)
    rules = _validate_rules(body.rules)
    await store.set_rules(
        store.SUBJECT_USER,
        user_id,
        rules,
        tenant_id=_tenant_id(),
        actor=auth.identifier,
    )
    await _audit(auth, "user.rules.update", "user", user_id, {"rules": rules})
    return {
        "status": "success",
        "rules": await store.get_rules(
            store.SUBJECT_USER, user_id, tenant_id=_tenant_id()
        ),
    }


@router.get("/users/{user_id}/effective-access")
async def get_effective_access(user_id: str) -> Dict[str, Any]:
    """What the user may use, per resource type, and which rules decide it.

    For each editable type: ``limited`` (False = no rule anywhere, no limit),
    ``allowed`` (effective ids, ``null`` = all) and ``sources`` (the user's
    direct rule and each group's rule, ``rule: null`` when that subject has no
    rule for the type and therefore does not count).
    """
    await _get_user_or_404(user_id)
    tenant = _tenant_id()
    principal = await load_principal(user_id, tenant)
    direct = await store.get_rules(store.SUBJECT_USER, user_id, tenant_id=tenant)
    groups = await store.list_user_groups(user_id, tenant)
    group_rules = {
        g.id: await store.get_rules(store.SUBJECT_GROUP, g.id, tenant_id=tenant)
        for g in groups
    }
    out: Dict[str, Any] = {}
    for rt in store.EDITABLE_RESOURCE_TYPES:
        sources = [
            {"kind": "user", "id": user_id, "name": None, "rule": direct.get(rt)}
        ]
        sources += [
            {
                "kind": "group",
                "id": g.id,
                "name": g.name,
                "rule": group_rules[g.id].get(rt),
            }
            for g in groups
        ]
        allowed = principal.allowed_ids(rt)
        out[rt] = {
            "limited": any(s["rule"] is not None for s in sources),
            "allowed": list(allowed) if allowed is not None else None,
            "sources": sources,
        }
    return {
        "user_id": user_id,
        "groups": [_group_out(g) for g in groups],
        "access": out,
    }
