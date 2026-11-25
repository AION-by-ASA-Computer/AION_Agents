"""Private profiles of the calling user (access control, Phase 3).

Endpoints used by chat-ui → "I miei profili":

- ``GET    /v1/me/profiles``                    list (with ``can_create`` and limits)
- ``GET    /v1/me/profiles/building-blocks``    skills / MCP servers / native tools
  the user may add, with names and descriptions (from ``src.access.catalog``)
- ``POST   /v1/me/profiles``                    create (slug generated server-side)
- ``GET    /v1/me/profiles/{slug}``             read one
- ``PUT    /v1/me/profiles/{slug}``             replace the editable fields
- ``DELETE /v1/me/profiles/{slug}``             delete (pauses the cron jobs using it)
- ``POST   /v1/me/profiles/{slug}/duplicate``   copy any profile the user may use
- ``POST   /v1/me/profiles/wizard-generate``    AI draft of a new profile
- ``POST   /v1/me/profiles/wizard-refine``      AI update of the profile being edited

The wizard reuses ``src.runtime.wizard_generator`` (the admin-ui wizard)
restricted to the skills / MCP servers the caller may add; it returns drafts
only, saving always goes through the validated create / update endpoints.

Thin HTTP layer: rules, validation and storage are in
``src.access.private_profiles``. API keys act as their owner, like everywhere.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.access import private_profiles as pp
from src.access.catalog import resource_catalog
from src.access.policy import ProfileAccessDenied, filter_allowed, resolve_profile_for
from src.access.principal import Principal, load_principal_for
from src.api.auth_login import ChatAuthIdentity, require_chat_auth

logger = logging.getLogger("aion.api.me_profiles")

router = APIRouter(prefix="/me/profiles", tags=["v1-me-profiles"])


class ProfileBody(BaseModel):
    """Editable fields; the lists hold ids from ``building-blocks``."""

    name: str = Field(..., min_length=1, max_length=pp.MAX_NAME)
    description: str = Field("", max_length=pp.MAX_DESCRIPTION)
    instructions: str = ""
    skills: List[str] = Field(default_factory=list)
    mcp_servers: List[str] = Field(default_factory=list)
    native_tool_groups: List[str] = Field(default_factory=list)


class DuplicateBody(BaseModel):
    """Name of the copy; defaults to the source name."""

    name: Optional[str] = Field(None, max_length=pp.MAX_NAME)


async def _principal(auth: ChatAuthIdentity) -> Principal:
    """Caller's principal; anonymous callers cannot own profiles (401)."""
    principal = await load_principal_for(auth.user_row_id or auth.identifier)
    if not principal.exists or not principal.user_row_id:
        raise HTTPException(401, detail={"code": "authentication_required"})
    return principal


def _http(err: pp.PrivateProfileError) -> HTTPException:
    """Map a domain error to its HTTP status and ``{code, ...}`` body."""
    extra = dict(err.extra)
    code = extra.pop("code", None) or err.code
    return HTTPException(
        err.status, detail={"code": code, "message": str(err), **extra}
    )


def _input(body: ProfileBody) -> pp.ProfileInput:
    return pp.ProfileInput(**body.model_dump())


@router.get("")
async def list_my_profiles(
    auth: ChatAuthIdentity = Depends(require_chat_auth),
) -> Dict[str, Any]:
    """The caller's private profiles plus what the UI needs to offer creation."""
    principal = await _principal(auth)
    rows = await pp.list_owned(principal)
    return {
        "items": [pp.profile_to_dict(r, principal) for r in rows],
        "can_create": pp.can_create(principal),
        "limits": {
            "max_profiles": pp.max_profiles_per_user(),
            "max_instructions": pp.max_instructions_chars(),
        },
    }


@router.get("/building-blocks")
async def get_building_blocks(
    auth: ChatAuthIdentity = Depends(require_chat_auth),
) -> Dict[str, Any]:
    """Items the caller may put in a private profile, per kind.

    ``{"skills": [{id, name, description}], "mcp_servers": [...],
    "native_tool_groups": [...]}``, already filtered by the caller's rules.
    """
    principal = await _principal(auth)
    out: Dict[str, Any] = {}
    for part, rt in (
        ("skills", "skill"),
        ("mcp_servers", "mcp_server"),
        ("native_tool_groups", "native_tool_group"),
    ):
        catalog = resource_catalog(rt)
        allowed = set(filter_allowed(principal, rt, [c["id"] for c in catalog]))
        out[part] = [c for c in catalog if c["id"] in allowed]
    return out


@router.post("", status_code=201)
async def create_my_profile(
    body: ProfileBody, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """Create a private profile; the response carries the generated slug."""
    principal = await _principal(auth)
    try:
        row = await pp.create(principal, _input(body))
    except pp.PrivateProfileError as err:
        raise _http(err) from None
    return pp.profile_to_dict(row, principal)


@router.get("/{slug}")
async def get_my_profile(
    slug: str, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """One private profile of the caller (404 for anybody else's)."""
    principal = await _principal(auth)
    try:
        row = await pp.get_owned(principal, slug)
    except pp.PrivateProfileError as err:
        raise _http(err) from None
    return pp.profile_to_dict(row, principal)


@router.put("/{slug}")
async def update_my_profile(
    slug: str, body: ProfileBody, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """Replace the editable fields (the slug stays the same)."""
    principal = await _principal(auth)
    try:
        row = await pp.update(principal, slug, _input(body))
    except pp.PrivateProfileError as err:
        raise _http(err) from None
    return pp.profile_to_dict(row, principal)


@router.delete("/{slug}")
async def delete_my_profile(
    slug: str, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """Delete a private profile; ``paused_jobs`` = cron jobs switched off."""
    principal = await _principal(auth)
    try:
        paused = await pp.delete_owned(principal, slug)
    except pp.PrivateProfileError as err:
        raise _http(err) from None
    return {"status": "success", "paused_jobs": paused}


@router.post("/{slug}/duplicate", status_code=201)
async def duplicate_profile(
    slug: str,
    body: DuplicateBody,
    auth: ChatAuthIdentity = Depends(require_chat_auth),
) -> Dict[str, Any]:
    """New private profile copied from any profile the caller may use
    (a global one or their own); items they may not add are left out."""
    from src.agent_profile import ProfileNotFoundError, profile_manager

    principal = await _principal(auth)
    source = profile_manager.get_profile(slug)
    if source is None:
        raise HTTPException(404, detail={"code": "profile_not_found"})
    try:
        resolve_profile_for(principal, source.slug)
    except (ProfileAccessDenied, ProfileNotFoundError):
        raise HTTPException(404, detail={"code": "profile_not_found"}) from None
    data = pp.duplicate_input(principal, source, (body.name or source.name).strip())
    try:
        row = await pp.create(principal, data)
    except pp.PrivateProfileError as err:
        raise _http(err) from None
    return pp.profile_to_dict(row, principal)


# --- AI wizard (same generator as the admin-ui profile wizard) -------------------


class WizardGenerateBody(BaseModel):
    """Natural-language description of the profile to build."""

    prompt: str = Field(..., min_length=1, max_length=4000)
    messages: Optional[List[Dict[str, str]]] = Field(None, max_length=20)


class WizardRefineBody(BaseModel):
    """Change request plus the editor's current (unsaved) state."""

    prompt: str = Field(..., min_length=1, max_length=4000)
    current_profile: ProfileBody


def _wizard_scope(principal: Principal) -> tuple:
    """Skills and MCP servers the caller may add: the only ones the wizard
    sees and may return (the wizard itself is not gated by profile rules)."""
    skills = set(
        filter_allowed(principal, "skill", [c["id"] for c in resource_catalog("skill")])
    )
    mcps = set(
        filter_allowed(
            principal, "mcp_server", [c["id"] for c in resource_catalog("mcp_server")]
        )
    )
    return skills, mcps


def _draft(raw: Dict[str, Any], native_tool_groups: List[str]) -> Dict[str, Any]:
    """Wizard output as an unsaved draft for the editor. The wizard's slug is
    dropped (private slugs are generated on create) and instructions are cut
    to the per-user limit so the draft can always be saved."""
    return {
        "name": str(raw.get("name") or "")[: pp.MAX_NAME],
        "description": str(raw.get("description") or "")[: pp.MAX_DESCRIPTION],
        "instructions": str(raw.get("instructions") or "")[
            : pp.max_instructions_chars()
        ],
        "skills": list(raw.get("skills") or []),
        "mcp_servers": list(raw.get("mcp_servers") or []),
        "native_tool_groups": native_tool_groups,
    }


@router.post("/wizard-generate")
async def wizard_generate(
    body: WizardGenerateBody, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """Draft of a new private profile from a description (nothing is saved).

    Needs ``capability:private_profiles.create``: the draft is meant to be
    created right after with ``POST /v1/me/profiles``.
    """
    from src.runtime.wizard_generator import generate_profile_wizard

    principal = await _principal(auth)
    if not pp.can_create(principal):
        raise _http(pp.CreateNotAllowed())
    skills, mcps = _wizard_scope(principal)
    try:
        raw = await generate_profile_wizard(
            body.prompt, history=body.messages, allowed_skills=skills, allowed_mcps=mcps
        )
    except Exception as exc:  # LLM / parsing errors: the draft is optional
        logger.warning("private profile wizard failed: %s", exc, exc_info=True)
        raise HTTPException(502, detail={"code": "wizard_failed"}) from None
    return _draft(raw, [])


@router.post("/wizard-refine")
async def wizard_refine(
    body: WizardRefineBody, auth: ChatAuthIdentity = Depends(require_chat_auth)
) -> Dict[str, Any]:
    """Updated draft of a profile being edited (nothing is saved).

    Native tool groups are not handled by the wizard: the current ones the
    caller may still use are kept.
    """
    from src.runtime.wizard_generator import refine_profile_wizard

    principal = await _principal(auth)
    skills, mcps = _wizard_scope(principal)
    current = body.current_profile.model_dump()
    # Items the caller may no longer add never reach the LLM.
    current["skills"] = [s for s in current["skills"] if s in skills]
    current["mcp_servers"] = [m for m in current["mcp_servers"] if m in mcps]
    try:
        raw = await refine_profile_wizard(
            body.prompt, current, allowed_skills=skills, allowed_mcps=mcps
        )
    except Exception as exc:
        logger.warning("private profile wizard failed: %s", exc, exc_info=True)
        raise HTTPException(502, detail={"code": "wizard_failed"}) from None
    native = filter_allowed(
        principal, "native_tool_group", current["native_tool_groups"]
    )
    return _draft(raw, native)
