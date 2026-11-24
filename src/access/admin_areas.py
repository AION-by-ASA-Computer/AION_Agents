"""Admin panel areas and the route table that protects ``/admin/*`` (Phase 5).

The admin panel is split into five delegable **areas**, each granted at level
``read`` or ``write`` (``write`` includes ``read``):

- ``access``        menu "Accessi": users (accounts, login mode / SSO / 2FA,
                    groups, migration), service accounts, security scans;
- ``agents``        menu "Agenti" (profiles, skills, memory, scheduled jobs);
- ``settings``      the Settings page (models / LLM providers, memory, web,
                    speech-to-text, sandbox, SMTP, file server, ...): kept
                    apart from ``agents`` because it changes the whole
                    platform, so it can be granted on its own;
- ``external``      menu "Piattaforma" (WhatsApp, Telegram) and the MCP Hub;
- ``observability`` menu "Osservabilità" (metrics, feedback, diagnostics,
                    system).

The "Panoramica" (dashboard) is open to everyone who may enter the panel.
Super-admins (``admin`` role) hold every area in write. Unlike "use" rules,
**no rule means no area**: areas must be granted explicitly
(``PLAN_access_control.md``, Phase 5).

Storage: ``access_grants`` rows with ``resource_type="admin_section"``,
``action="use"`` and ``resource_id="<area>:<level>"`` (e.g. ``agents:write``),
so rules reuse the generic rule model of ``src/access/store.py``. Several
rules (direct + groups) add up: the highest level per area wins.

``route_requirement`` maps every ``/admin/*`` request (method + path) to what
it needs; ``src.api.auth_login.require_admin_role`` applies it to every admin
router. A route missing from the table is super-admin only, and a test
(``test_admin_rbac.py``) fails when a route is not mapped explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

AREA_ACCESS = "access"
AREA_AGENTS = "agents"
AREA_SETTINGS = "settings"
AREA_EXTERNAL = "external"
AREA_OBSERVABILITY = "observability"
AREAS: Tuple[str, ...] = (
    AREA_ACCESS,
    AREA_AGENTS,
    AREA_SETTINGS,
    AREA_EXTERNAL,
    AREA_OBSERVABILITY,
)

LEVEL_READ = "read"
LEVEL_WRITE = "write"
_RANK = {LEVEL_READ: 1, LEVEL_WRITE: 2}

# Capability that gives the *base* panel access (dashboard). A rule excluding
# it = "Solo chat UI"; areas granted by other rules still open the panel
# (permissions add up, decision 1 of Phase 5).
CAPABILITY_ADMIN_PANEL = "admin_panel.access"


def grant_ids() -> Tuple[str, ...]:
    """Every valid ``resource_id`` of an ``admin_section`` rule."""
    return tuple(f"{a}:{lv}" for a in AREAS for lv in (LEVEL_READ, LEVEL_WRITE))


def parse_grant(resource_id: str) -> Optional[Tuple[str, str]]:
    """``"agents:write"`` -> ``("agents", "write")``; ``None`` if invalid."""
    area, _, level = (resource_id or "").partition(":")
    if area in AREAS and level in _RANK:
        return area, level
    return None


def combine(grants: Optional[Iterable[str]]) -> Dict[str, str]:
    """Effective ``{area: level}`` from granted ids (highest level wins)."""
    out: Dict[str, str] = {}
    for gid in grants or ():
        parsed = parse_grant(gid)
        if parsed is None:
            continue
        area, level = parsed
        if _RANK[level] > _RANK.get(out.get(area, ""), 0):
            out[area] = level
    return out


def level_allows(level: Optional[str], action: str) -> bool:
    """True if ``level`` (``read``/``write``/``None``) covers ``action``."""
    return _RANK.get(level or "", 0) >= _RANK.get(action, 99)


# --- runtime settings (/admin/settings) ------------------------------------------

# ``/admin/settings`` stores every runtime setting (``data/runtime.env``), but
# not all of them belong to the Settings page: the observability dialog of the
# Metrics page writes Prometheus / OpenTelemetry / Opik keys and the Scheduled
# jobs page the scheduler switch through the same endpoint. Each key is
# therefore checked against the area that owns it.
OBSERVABILITY_SETTING_PREFIXES: Tuple[str, ...] = (
    "AION_PROMETHEUS_",
    "AION_METRICS_",
    "AION_OTEL_",
    "AION_OPIK_",
    "OPIK_",
)

# Non-secret keys any panel user may read: the dashboard (OCR / embeddings
# nodes, sandbox) and the MCP Hub (sandbox backend) show them.
PANEL_SETTING_KEYS = frozenset(
    {
        "AION_SANDBOX_BACKEND",
        "AION_OCR_BASE_URL",
        "AION_OCR_MODEL",
        "AION_EMBEDDING_URL",
        "AION_EMBEDDING_MODEL",
    }
)


# Keys owned by pages of the "agents" area (Scheduled jobs: scheduler switch).
AGENTS_SETTING_KEYS = frozenset({"AION_CRON_ENABLED"})


def setting_area(key: str) -> str:
    """Area that owns a runtime setting: ``observability``, ``agents`` or
    ``settings`` (everything else, the Settings page)."""
    if key.upper().startswith(OBSERVABILITY_SETTING_PREFIXES):
        return AREA_OBSERVABILITY
    if key in AGENTS_SETTING_KEYS:
        return AREA_AGENTS
    return AREA_SETTINGS


def readable_setting(key: str, areas: Dict[str, str]) -> bool:
    """True if a caller with ``areas`` may read ``key`` from ``/admin/settings``."""
    if key in PANEL_SETTING_KEYS:
        return True
    return level_allows(areas.get(setting_area(key)), LEVEL_READ)


# --- route table ---------------------------------------------------------------

# Requirement kinds.
KIND_AREA = "area"  # one of ``areas`` at ``action``
KIND_PANEL = "panel"  # anybody who may enter the admin panel (dashboard data)
KIND_SUPERADMIN = "superadmin"  # never delegable


@dataclass(frozen=True)
class Requirement:
    """What a request needs: ``kind`` plus, for ``area``, the accepted areas
    (any of them is enough) and the action (``read`` / ``write``)."""

    kind: str
    areas: Tuple[str, ...] = ()
    action: str = LEVEL_READ


def _method_action(method: str) -> str:
    return LEVEL_READ if method.upper() in ("GET", "HEAD", "OPTIONS") else LEVEL_WRITE


# Ordered rules: (methods or None for all, path regex, kind, areas, action or
# None = from the method). First match wins; paths are relative to the API
# root (``/admin/...``), path parameters matched by ``[^/]+``.
_A, _G, _S, _E, _O = (
    AREA_ACCESS,
    AREA_AGENTS,
    AREA_SETTINGS,
    AREA_EXTERNAL,
    AREA_OBSERVABILITY,
)
_RULES: Tuple[
    Tuple[Optional[Tuple[str, ...]], str, str, Tuple[str, ...], Optional[str]], ...
] = (
    # Never delegable: one-click update of the whole stack.
    (None, r"/admin/update(/.*)?", KIND_SUPERADMIN, (), None),
    # Dashboard ("Panoramica"): open to every panel user.
    (None, r"/admin/(dashboard|stats|version-check)(/.*)?", KIND_PANEL, (), LEVEL_READ),
    # --- explicit overrides (before the prefix rules) ---
    # Reading an MCP secret value is a write-level operation of the Hub.
    (("GET",), r"/admin/mcp/[^/]+/secret/[^/]+", KIND_AREA, (_E,), LEVEL_WRITE),
    # Starting a diagnostics run is an action even if exposed as GET (SSE).
    (("GET",), r"/admin/diagnostics/run-tests", KIND_AREA, (_O,), LEVEL_WRITE),
    # Security investigation runs an agent and saves a report (SSE over GET).
    (("GET",), r"/admin/security/investigate(/.*)?", KIND_AREA, (_A,), LEVEL_WRITE),
    # Catalogues read by the profile editor (agents) and by the Hub (external).
    (
        ("GET",),
        r"/admin/(registry|native-tools|mcp-integrations|mcp/connector-catalog)(/.*)?",
        KIND_AREA,
        (_G, _E),
        LEVEL_READ,
    ),
    # Runtime settings: read by the dashboard, Hub, Metrics, Scheduled jobs and
    # Settings pages (keys filtered per area in src/api/settings_api.py);
    # written by the Settings page (settings), the observability dialog
    # (observability) and the scheduler switch (agents), each changed key
    # checked against its own area by the endpoint.
    (("GET",), r"/admin/settings", KIND_PANEL, (), LEVEL_READ),
    (None, r"/admin/settings", KIND_AREA, (_S, _G, _O), None),
    # Profile list: read by the access rules editors and service accounts
    # (access), the Telegram / WhatsApp bindings (external) and the profile
    # filters of Metrics / Diagnostics (observability).
    (("GET",), r"/admin/profiles", KIND_AREA, (_G, _A, _E, _O), LEVEL_READ),
    # User list (not single users): user filter of the Metrics page.
    (("GET",), r"/admin/users", KIND_AREA, (_A, _O), LEVEL_READ),
    # SMTP status: the Users page shows whether invites go out by email.
    (("GET",), r"/admin/smtp", KIND_AREA, (_S, _A), LEVEL_READ),
    # Knowledge projects: picker of the Telegram assistant settings.
    (("GET",), r"/admin/query-memory/projects", KIND_AREA, (_G, _E), LEVEL_READ),
    # MCP security scan: started from the Security page (access) or the Hub.
    (("POST",), r"/admin/mcp/scan", KIND_AREA, (_A, _E), LEVEL_WRITE),
    # --- access ---
    (
        None,
        r"/admin/(users|groups|service-accounts|security|auth|sso|admin/sso|access)(/.*)?",
        KIND_AREA,
        (_A,),
        None,
    ),
    # --- agents (menu Agenti) ---
    (
        None,
        r"/admin/(profiles|skills|memory|ltm|stm|profile-memory|query-memory|cron-jobs|subagents)(/.*)?",
        KIND_AREA,
        (_G,),
        None,
    ),
    # --- settings (Settings page tabs) ---
    (
        None,
        r"/admin/(settings|llm-providers|stt|smtp|smb)(/.*)?",
        KIND_AREA,
        (_S,),
        None,
    ),
    # --- external (MCP Hub + Piattaforma) ---
    (
        None,
        r"/admin/(mcp|mcp-integrations|registry|market|whatsapp|telegram)(/.*)?",
        KIND_AREA,
        (_E,),
        None,
    ),
    # --- observability ---
    (
        None,
        r"/admin/(metrics|feedback|diagnostics|system|profiling)(/.*)?",
        KIND_AREA,
        (_O,),
        None,
    ),
)
_COMPILED = tuple(
    (methods, re.compile(f"^{pattern}$"), kind, areas, action)
    for methods, pattern, kind, areas, action in _RULES
)


def route_requirement(method: str, path: str) -> Optional[Requirement]:
    """Requirement of an admin request, ``None`` when the route is unmapped
    (callers treat that as super-admin only)."""
    clean = "/" + path.strip("/")
    for methods, rx, kind, areas, action in _COMPILED:
        if methods is not None and method.upper() not in methods:
            continue
        if rx.match(clean):
            return Requirement(kind, areas, action or _method_action(method))
    return None
