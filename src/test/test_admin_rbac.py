"""Phase 5 of the access-control plan: admin panel areas (read / write).

Covers:
- the route table: every ``/admin/*`` route of the app is mapped to an area,
  the dashboard or super-admin only;
- the policy: no rule = no area, rules add up (highest level per area, areas
  from different groups), "chat-ui only" vs areas granted elsewhere;
- the HTTP guard ``require_admin_role`` end to end with real chat tokens
  (read vs write, other areas, dashboard, super-admin only routes, API keys);
- the anti-escalation rules (self, own group, super-admin targets, admin
  role reserved) and the validation of ``admin_section`` rules.

Reuses the isolated-DB helpers of ``test_access_groups.py``.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException

from src.access import store
from src.access.principal import load_principal
from test_access_groups import ADMIN, ANNA, BRUNO, _admin, _setup


def _ctx(row_id: str, ident: str, roles=()):
    """Chat-token identity as built by ``require_chat_auth``."""
    from src.api.auth_login import ChatAuthIdentity

    return ChatAuthIdentity(
        user_row_id=row_id, identifier=ident, via="chat_token", roles=list(roles)
    )


# --- route table ---------------------------------------------------------------


def test_every_admin_route_is_mapped():
    """A new ``/admin/*`` route must be added to the table in
    ``src/access/admin_areas.py`` (unmapped = super-admin only)."""
    from src.access.admin_areas import route_requirement
    from src.api.main import app

    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin/"):
            continue
        concrete = path.replace("{", "x").replace("}", "")
        for method in getattr(route, "methods", None) or {"GET"}:
            if route_requirement(method, concrete) is None:
                missing.append(f"{method} {path}")
    assert missing == []


def test_route_table_samples():
    """Spot checks of the decisions in the plan (areas, overrides, dashboard)."""
    from src.access.admin_areas import route_requirement as r

    assert r("GET", "/admin/users/u1").areas == ("access",)
    # The bare user list is also read by the Metrics user filter.
    assert set(r("GET", "/admin/users").areas) == {"access", "observability"}
    assert r("POST", "/admin/users").areas == ("access",)
    assert r("PUT", "/admin/auth/login-mode").areas == ("access",)
    assert r("POST", "/admin/cron-jobs").areas == ("agents",)
    assert r("POST", "/admin/cron-jobs").action == "write"
    assert r("GET", "/admin/settings").kind == "panel"
    assert set(r("POST", "/admin/settings").areas) == {
        "settings",
        "agents",
        "observability",
    }
    # The Settings page is an area of its own, apart from "agents".
    assert r("GET", "/admin/settings/fs-policy").areas == ("settings",)
    assert r("PUT", "/admin/smtp").areas == ("settings",)
    assert r("POST", "/admin/llm-providers").areas == ("settings",)
    assert r("POST", "/admin/smb/servers").areas == ("settings",)
    assert r("GET", "/admin/profiles/x").areas == ("agents",)
    assert r("GET", "/admin/mcp/x/secret/KEY").action == "write"
    assert set(r("GET", "/admin/registry").areas) == {"agents", "external"}
    assert r("POST", "/admin/mcp/install").areas == ("external",)
    assert r("GET", "/admin/telegram/settings").areas == ("external",)
    assert r("GET", "/admin/metrics/overview").areas == ("observability",)
    assert r("GET", "/admin/diagnostics/run-tests").action == "write"
    assert r("GET", "/admin/security/investigate").action == "write"
    assert r("GET", "/admin/stats").kind == "panel"
    assert r("POST", "/admin/update/start").kind == "superadmin"


# --- policy ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_areas_default_to_none_and_add_up(monkeypatch, tmp_path):
    """No rule = no area; read + write of the same area = write; areas from
    different groups add up; super-admins hold every area."""
    await _setup(monkeypatch, tmp_path)
    from src.access.policy import admin_areas, can, can_enter_admin_panel

    anna = await load_principal(ANNA)
    assert admin_areas(anna) == {}
    assert can_enter_admin_panel(anna)  # base panel access (dashboard only)
    assert not can(anna, "read", "admin_section", "agents")

    g1 = await store.create_group(slug="readers", name="Readers")
    g2 = await store.create_group(slug="ops", name="Ops")
    await store.set_members(g1.id, [ANNA])
    await store.set_members(g2.id, [ANNA])
    await store.set_rules(
        store.SUBJECT_GROUP, g1.id, {"admin_section": ["agents:read"]}
    )
    await store.set_rules(
        store.SUBJECT_GROUP, g2.id, {"admin_section": ["agents:write", "external:read"]}
    )
    anna = await load_principal(ANNA)
    assert admin_areas(anna) == {"agents": "write", "external": "read"}
    assert can(anna, "write", "admin_section", "agents")
    assert not can(anna, "write", "admin_section", "external")

    assert admin_areas(await load_principal(ADMIN)) == {
        "access": "write",
        "agents": "write",
        "settings": "write",
        "external": "write",
        "observability": "write",
    }


@pytest.mark.anyio
async def test_chat_only_loses_to_granted_areas(monkeypatch, tmp_path):
    """ "Solo chat UI" (capability rule without admin_panel.access) closes the
    panel, unless another rule grants an area: permissions add up."""
    await _setup(monkeypatch, tmp_path)
    from src.access.policy import can_enter_admin_panel

    await store.set_rules(
        store.SUBJECT_USER, BRUNO, {"capability": ["private_profiles.create"]}
    )
    assert not can_enter_admin_panel(await load_principal(BRUNO))

    g = await store.create_group(slug="watchers", name="Watchers")
    await store.set_members(g.id, [BRUNO])
    await store.set_rules(
        store.SUBJECT_GROUP, g.id, {"admin_section": ["observability:read"]}
    )
    assert can_enter_admin_panel(await load_principal(BRUNO))


# --- HTTP guard --------------------------------------------------------------------


def _mini_app():
    """Tiny app with a few admin routes behind the real guard."""
    from src.api.auth_login import require_admin_role

    router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_role)])

    @router.get("/skills")
    async def skills_get():
        return {"ok": True}

    @router.post("/skills")
    async def skills_post():
        return {"ok": True}

    @router.get("/users")
    async def users_get():
        return {"ok": True}

    @router.get("/stats")
    async def stats_get():
        return {"ok": True}

    @router.get("/update/status")
    async def update_get():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.anyio
async def test_admin_guard_over_http(monkeypatch, tmp_path):
    """Real chat tokens through ``require_admin_role``: area and level are
    enforced per route; super-admin only routes stay closed; API keys of a
    super-admin never open the panel."""
    await _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("AION_CHAT_PASSWORD_AUTH", "1")
    monkeypatch.setenv("AION_ADMIN_PASSWORD_AUTH", "1")
    monkeypatch.setenv("AION_CHAT_AUTH_SECRET", "s" * 40)
    import httpx

    from src.api.auth_login import issue_chat_token

    await store.set_rules(store.SUBJECT_USER, ANNA, {"admin_section": ["agents:read"]})
    anna = {
        "Authorization": "Bearer "
        + issue_chat_token(user_row_id=ANNA, user_identifier="anna")
    }
    boss = {
        "Authorization": "Bearer "
        + issue_chat_token(user_row_id=ADMIN, user_identifier="boss", roles=["admin"])
    }
    transport = httpx.ASGITransport(app=_mini_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.get("/admin/skills", headers=anna)).status_code == 200
        denied = await c.post("/admin/skills", headers=anna)
        assert denied.status_code == 403
        assert denied.json()["detail"]["action"] == "write"
        assert (await c.get("/admin/users", headers=anna)).status_code == 403
        assert (await c.get("/admin/stats", headers=anna)).status_code == 200
        assert (await c.get("/admin/update/status", headers=anna)).status_code == 403
        for path in ("/admin/users", "/admin/update/status"):
            assert (await c.get(path, headers=boss)).status_code == 200
        assert (await c.get("/admin/stats")).status_code == 401

    from src.api.auth_login import ChatAuthIdentity, require_admin_role

    # API key of a super-admin: no roles, no areas -> panel closed.
    with pytest.raises(HTTPException) as exc:
        await require_admin_role(
            ChatAuthIdentity(user_row_id=ADMIN, via="api_key", roles=[])
        )
    assert exc.value.status_code == 403


def test_settings_keys_follow_their_area():
    """``/admin/settings`` is shared by the Settings page (own "settings"
    area), the observability dialog of Metrics and the scheduler switch of
    Scheduled jobs (agents): each changed key needs write on its own area,
    unchanged keys are ignored, reads are filtered per area."""
    from src.access.admin_areas import readable_setting, setting_area
    from src.api.settings_api import _ensure_can_write_settings

    assert setting_area("AION_OTEL_ENDPOINT") == "observability"
    assert setting_area("OPIK_API_KEY") == "observability"
    assert setting_area("AION_CRON_ENABLED") == "agents"
    assert setting_area("AION_MODEL") == "settings"

    existing = {"AION_OTEL_ENABLED": "0", "AION_MODEL": "m1", "AION_CRON_ENABLED": "1"}
    obs_read_settings_write = {"settings": "write", "observability": "read"}
    # Observability read + settings write cannot save observability keys.
    with pytest.raises(HTTPException) as exc:
        _ensure_can_write_settings(
            obs_read_settings_write, {"AION_OTEL_ENABLED": "1"}, existing
        )
    assert exc.value.detail["areas"] == ["observability"]
    # Settings page re-sending an unchanged observability key: allowed.
    _ensure_can_write_settings(
        obs_read_settings_write,
        {"AION_OTEL_ENABLED": "0", "AION_MODEL": "m2"},
        existing,
    )
    # "Gestione agenti" alone no longer opens the Settings page keys ...
    with pytest.raises(HTTPException) as exc:
        _ensure_can_write_settings({"agents": "write"}, {"AION_MODEL": "m2"}, existing)
    assert exc.value.detail["areas"] == ["settings"]
    # ... but still drives the scheduler switch of its own page.
    _ensure_can_write_settings(
        {"agents": "write"}, {"AION_CRON_ENABLED": "0"}, existing
    )
    # Observability writer cannot change other settings through the dialog.
    with pytest.raises(HTTPException):
        _ensure_can_write_settings(
            {"observability": "write"}, {"AION_MODEL": "m2"}, existing
        )
    _ensure_can_write_settings(
        {"observability": "write"}, {"AION_OTEL_ENABLED": "1"}, existing
    )
    # Super-admin / auth off.
    _ensure_can_write_settings(None, {"AION_MODEL": "x"}, existing)

    # Reads: own areas + the dashboard keys only.
    obs = {"observability": "read"}
    assert readable_setting("AION_OTEL_ENDPOINT", obs)
    assert readable_setting("AION_SANDBOX_BACKEND", obs)
    assert not readable_setting("AION_API_URL", obs)
    assert readable_setting("AION_API_URL", {"settings": "read"})
    assert not readable_setting("AION_API_URL", {"agents": "write"})


@pytest.mark.anyio
async def test_partial_settings_post_keeps_other_overrides(monkeypatch, tmp_path):
    """A POST with only some keys (observability dialog, scheduler switch)
    must not wipe the other admin overrides of ``data/runtime.env``."""
    from src.api import settings_api
    from src.api.auth_login import ChatAuthIdentity
    from src.runtime.env_sync import load_runtime_overrides

    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AION_ADMIN_PASSWORD_AUTH", "0")
    # No process reload / container restart in tests.
    monkeypatch.setattr(settings_api, "_reload_env", lambda: None)
    monkeypatch.setattr(settings_api, "_is_container_env", lambda: False)
    auth = ChatAuthIdentity(user_row_id="u", via="chat_token", roles=["admin"])

    await settings_api.update_settings(
        settings_api.SettingsUpdate(settings={"AION_MODEL": "model-kept-by-test"}),
        auth=auth,
    )
    await settings_api.update_settings(
        settings_api.SettingsUpdate(settings={"AION_OTEL_SERVICE_NAME": "svc-test"}),
        auth=auth,
    )
    runtime = load_runtime_overrides(data_dir=tmp_path)
    assert runtime.get("AION_MODEL") == "model-kept-by-test"
    assert runtime.get("AION_OTEL_SERVICE_NAME") == "svc-test"


# --- anti-escalation ---------------------------------------------------------------


@pytest.mark.anyio
async def test_access_writers_cannot_escalate(monkeypatch, tmp_path):
    """A non-super-admin with access:write manages others but never
    themself, their own groups, super-admins or the admin role."""
    await _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("AION_ADMIN_PASSWORD_AUTH", "1")
    from src.api import admin as admin_api
    from src.api import admin_groups as api

    await store.set_rules(store.SUBJECT_USER, ANNA, {"admin_section": ["access:write"]})
    anna = _ctx(ANNA, "anna")

    def reason(exc_info):
        return exc_info.value.detail["reason"]

    # Others: allowed, including giving every area.
    await api.set_user_rules(
        BRUNO,
        api.RulesUpdate(rules={"admin_section": ["access:write", "agents:write"]}),
        auth=anna,
    )
    # Self.
    with pytest.raises(HTTPException) as exc:
        await api.set_user_rules(
            ANNA, api.RulesUpdate(rules={"admin_section": ["agents:write"]}), auth=anna
        )
    assert reason(exc) == "self"
    # Super-admin targets.
    with pytest.raises(HTTPException) as exc:
        await api.set_user_rules(
            ADMIN, api.RulesUpdate(rules={"profile": None}), auth=anna
        )
    assert reason(exc) == "superadmin_target"
    with pytest.raises(HTTPException) as exc:
        await admin_api.update_user(
            ADMIN, admin_api.UserUpdate(display_name="x"), auth=anna
        )
    assert reason(exc) == "superadmin_target"
    # Admin role reserved.
    with pytest.raises(HTTPException) as exc:
        await admin_api.update_user(
            BRUNO, admin_api.UserUpdate(roles=["admin"]), auth=anna
        )
    assert reason(exc) == "admin_role_reserved"
    # Groups: joining one, editing an own group, a group with a super-admin.
    g = await api.create_group(api.GroupCreate(slug="team", name="Team"), auth=anna)
    with pytest.raises(HTTPException) as exc:
        await api.set_group_members(
            g["id"], api.MembersUpdate(user_ids=[ANNA]), auth=anna
        )
    assert reason(exc) == "own_group"
    with pytest.raises(HTTPException) as exc:
        await api.set_group_members(
            g["id"], api.MembersUpdate(user_ids=[ADMIN]), auth=anna
        )
    assert reason(exc) == "superadmin_target"
    await api.set_group_members(g["id"], api.MembersUpdate(user_ids=[BRUNO]), auth=anna)
    await api.set_group_rules(
        g["id"], api.RulesUpdate(rules={"admin_section": ["external:write"]}), auth=anna
    )
    await store.set_members(g["id"], [BRUNO, ANNA])  # e.g. done by a super-admin
    with pytest.raises(HTTPException) as exc:
        await api.set_group_rules(
            g["id"],
            api.RulesUpdate(rules={"admin_section": ["access:write"]}),
            auth=anna,
        )
    assert reason(exc) == "own_group"

    # A super-admin may do all of it.
    await api.set_group_rules(
        g["id"],
        api.RulesUpdate(rules={"admin_section": ["access:read"]}),
        auth=_admin(),
    )
    await admin_api.update_user(
        BRUNO, admin_api.UserUpdate(roles=["admin"]), auth=_admin()
    )


@pytest.mark.anyio
async def test_admin_section_rule_validation(monkeypatch, tmp_path):
    """Exact ids only (no "*", no patterns); read + write of one area keeps write."""
    await _setup(monkeypatch, tmp_path)
    from src.api import admin_groups as api

    auth = _admin()
    res = await api.set_user_rules(
        BRUNO,
        api.RulesUpdate(
            rules={"admin_section": ["agents:read", "agents:write", "access:read"]}
        ),
        auth=auth,
    )
    assert sorted(res["rules"]["admin_section"]) == ["access:read", "agents:write"]
    for bad in (["*"], ["agents:*"], ["agents"], ["billing:read"]):
        with pytest.raises(HTTPException) as exc:
            await api.set_user_rules(
                BRUNO, api.RulesUpdate(rules={"admin_section": bad}), auth=auth
            )
        assert exc.value.status_code == 422


# --- admin panel login / chat-ui handoff ---------------------------------------------


def _request():
    """Minimal request for endpoints that build public URLs."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
    )


@pytest.mark.anyio
async def test_chat_ui_handoff_opens_admin_panel_without_login(monkeypatch, tmp_path):
    """A user who may enter the panel (no admin role needed) gets a one-time
    code that the admin-ui exchanges for a session; "chat UI only" users and
    API keys are refused."""
    await _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("AION_ADMIN_UI_URL", "http://localhost:3870")
    from urllib.parse import unquote

    from src.api import auth_sso
    from src.api.auth_login import ChatAuthIdentity

    await store.set_rules(store.SUBJECT_USER, ANNA, {"admin_section": ["agents:write"]})
    res = await auth_sso.admin_handoff(
        _request(),
        auth_sso.AdminHandoffBody(return_to="/skills"),
        auth=_ctx(ANNA, "anna"),
    )
    assert "/login/sso#code=" in res["url"] and res["url"].endswith("return_to=/skills")
    code = unquote(res["url"].split("code=")[1].split("&")[0])
    session = await auth_sso.sso_exchange(auth_sso.ExchangeBody(code=code))
    assert session["access_token"] and session["identifier"] == "anna"
    with pytest.raises(HTTPException):  # one-time code
        await auth_sso.sso_exchange(auth_sso.ExchangeBody(code=code))

    await store.set_rules(store.SUBJECT_USER, BRUNO, {"capability": []})
    with pytest.raises(HTTPException) as exc:
        await auth_sso.admin_handoff(
            _request(), auth_sso.AdminHandoffBody(), auth=_ctx(BRUNO, "bruno")
        )
    assert exc.value.detail["code"] == "admin_panel_not_allowed"
    with pytest.raises(HTTPException) as exc:
        await auth_sso.admin_handoff(
            _request(),
            auth_sso.AdminHandoffBody(),
            auth=ChatAuthIdentity(user_row_id=ADMIN, via="api_key", roles=[]),
        )
    assert exc.value.status_code == 403
