"""Tests for Claude-style sub-agents (``delegate_to_agent``).

Covers the runner in ``src/runtime/subagent_orchestrator.py`` (allowlist,
access checks, depth 1, parallelism cap, timeout / stop handling, events sent
to the parent bus), the timeline merge of ``subagent_*`` events, the
read-only / hidden child conversation in the API layer, the profile schema
helpers and the always-blocked rule in plan / deep-research mode.
See docs/agents/subagents.md.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

import src.runtime.subagent_orchestrator as orch
from src.runtime.timeline_builder import TimelineBuilder


def _run(coro):
    return asyncio.run(coro)


def _profile(slug: str, *, subagents=None, description: str = "") -> SimpleNamespace:
    """Minimal stand-in for ``AgentProfile`` with the attributes the runner reads."""
    return SimpleNamespace(
        slug=slug,
        name=slug.replace("_", " ").title(),
        description=description,
        subagents=list(subagents or []),
    )


@pytest.fixture()
def profiles(monkeypatch):
    """Patch ``profile_manager.get_profile`` with an in-memory profile table.

    Unknown slugs fall back to the default profile, like the real resolver,
    so the tests exercise the "resolved slug must match" guard.
    """
    from src.agent_profile import profile_manager

    table: Dict[str, SimpleNamespace] = {
        "aion_std": _profile("aion_std", subagents=["legal", "sql", "ghost"]),
        "legal": _profile("legal", description="Contract and legal review."),
        "sql": _profile("sql", description="SQL analyst."),
    }
    monkeypatch.setattr(
        profile_manager, "get_profile", lambda slug: table.get(slug, table["aion_std"])
    )
    return table


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_child_session_id_format_and_depth_guard():
    """Child ids are ``sub_<slug>_<12 hex>`` and are recognised as sub-agent sessions."""
    sid = orch.new_child_session_id("Legal Reviewer!")
    assert sid.startswith("sub_legal-reviewer_")
    assert len(sid.rsplit("_", 1)[1]) == 12
    assert orch.is_subagent_session(sid)
    assert not orch.is_subagent_session("conv-123")
    assert not orch.is_subagent_session(None)


def test_truncate_result_keeps_newlines_head_and_tail():
    """Long answers keep line breaks, the head, the tail and an omission marker."""
    text = "\n".join(f"line {i}" for i in range(400))
    out = orch.truncate_result(text, max_chars=600)
    assert out.startswith("line 0\nline 1\n")
    assert out.rstrip().endswith("line 399")
    assert "characters omitted" in out
    assert orch.truncate_result("short\ntext", max_chars=600) == "short\ntext"


def test_tool_output_lists_status_text_and_files():
    """The parent LLM gets a header with status/conversation, the answer and the files."""
    res = orch.SubagentResult(
        "done", "legal", child_conversation_id="sub_legal_x", text="## Answer", files=["workspace/subagents/sub_legal_x/a.md"]
    )
    out = res.to_tool_output()
    assert out.startswith("[sub-agent legal · status=done · conversation=sub_legal_x]")
    assert "## Answer" in out
    assert "- workspace/subagents/sub_legal_x/a.md" in out
    denied = orch.SubagentResult("denied", "x", error="nope").to_tool_output()
    assert "status=denied" in denied and "Error: nope" in denied


# ---------------------------------------------------------------------------
# Catalog and access control
# ---------------------------------------------------------------------------


def test_catalog_skips_missing_profiles(profiles):
    """A slug that resolves to another profile (deleted / unknown) is not offered."""
    cat = orch.subagent_catalog(profiles["aion_std"])
    assert [c["slug"] for c in cat] == ["legal", "sql"]
    assert cat[0]["description"] == "Contract and legal review."


def test_allowed_slugs_intersect_user_access(profiles, monkeypatch):
    """The allowlist is intersected with the profiles the user may use."""
    import src.api.profile_access as pa

    async def _row_id(user_id, tenant_id="default"):
        return 7

    async def _allowed(row_id):
        return ["sql", "other"]

    monkeypatch.setattr(pa, "user_row_id_for", _row_id)
    monkeypatch.setattr(pa, "allowed_profile_slugs", _allowed)
    assert _run(orch.allowed_subagent_slugs(profiles["aion_std"], "alice")) == ["sql"]


def test_allowed_slugs_unrestricted_user_and_fail_closed(profiles, monkeypatch):
    """No per-profile restriction keeps the allowlist; an access-check error denies all."""
    import src.api.profile_access as pa

    async def _row_id(user_id, tenant_id="default"):
        return 7

    async def _unrestricted(row_id):
        return []

    monkeypatch.setattr(pa, "user_row_id_for", _row_id)
    monkeypatch.setattr(pa, "allowed_profile_slugs", _unrestricted)
    assert _run(orch.allowed_subagent_slugs(profiles["aion_std"], "alice")) == ["legal", "sql"]

    async def _boom(row_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(pa, "allowed_profile_slugs", _boom)
    assert _run(orch.allowed_subagent_slugs(profiles["aion_std"], "alice")) == []


def test_delegate_tool_schema_enum_and_empty_catalog(profiles):
    """The tool exposes only existing allowlisted slugs; no catalog means no tool."""
    from src.runtime.native_tools.factory_table import build_delegate_to_agent_tool

    tool = build_delegate_to_agent_tool("conv-1", "alice", profiles["aion_std"])
    assert tool.name == "delegate_to_agent"
    assert tool.parameters["properties"]["agent"]["enum"] == ["legal", "sql"]
    assert "legal (Legal): Contract and legal review." in tool.description
    with pytest.raises(ValueError):
        build_delegate_to_agent_tool("conv-1", "alice", _profile("lonely", subagents=["ghost"]))


# ---------------------------------------------------------------------------
# run_subagent guards and parallelism
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner_env(profiles, monkeypatch):
    """Stub the DB/access lookups of ``run_subagent`` (tenant, allowlist)."""

    async def _tenant(parent_session_id, user_id):
        return "default"

    async def _allowed(parent_profile, user_id, tenant_id="default"):
        return ["legal", "sql"]

    monkeypatch.setattr(orch, "_parent_tenant", _tenant)
    monkeypatch.setattr(orch, "allowed_subagent_slugs", _allowed)
    return profiles


def _call(profiles, **kw):
    args = dict(
        parent_session_id="conv-parent",
        parent_profile=profiles["aion_std"],
        user_id="alice",
        agent="legal",
        task="Review clause 4.",
    )
    args.update(kw)
    return orch.run_subagent(**args)


def test_run_subagent_denies_depth_two_and_unlisted(runner_env, monkeypatch):
    """A sub-agent cannot delegate; unlisted profiles and empty tasks are refused."""

    async def _never(**kw):  # pragma: no cover - must not be reached
        raise AssertionError("child turn must not start")

    monkeypatch.setattr(orch, "_run_child_turn", _never)
    nested = _run(_call(runner_env, parent_session_id="sub_legal_abc"))
    assert nested.status == "denied"
    unlisted = _run(_call(runner_env, agent="hr"))
    assert unlisted.status == "denied" and "allowed: legal, sql" in unlisted.error
    empty = _run(_call(runner_env, task="   "))
    assert empty.status == "error"


def test_run_subagent_denies_other_users_session(runner_env, monkeypatch):
    """Spawning from a conversation owned by someone else is refused."""

    async def _foreign(parent_session_id, user_id):
        raise PermissionError("session_not_owned")

    monkeypatch.setattr(orch, "_parent_tenant", _foreign)
    assert _run(_call(runner_env)).status == "denied"


def test_parallel_cap_per_parent_session(runner_env, monkeypatch):
    """At most AION_SUBAGENT_MAX_PARALLEL children run at once for one parent."""
    monkeypatch.setenv("AION_SUBAGENT_MAX_PARALLEL", "2")
    state = {"now": 0, "peak": 0}

    async def _child(**kw):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.05)
        state["now"] -= 1
        return orch.SubagentResult("done", kw["child_profile"].slug, text="ok")

    monkeypatch.setattr(orch, "_run_child_turn", _child)

    async def go():
        return await asyncio.gather(*[_call(runner_env) for _ in range(5)])

    results = _run(go())
    assert all(r.ok for r in results)
    assert state["peak"] == 2
    # Semaphores of idle parent sessions are dropped (no leak).
    assert "conv-parent" not in orch._SEMAPHORES


def test_stop_while_waiting_for_slot_cancels(runner_env, monkeypatch):
    """A call still queued when the parent is stopped never starts its child."""
    monkeypatch.setenv("AION_SUBAGENT_MAX_PARALLEL", "1")
    started: List[str] = []

    async def go():
        gate = asyncio.Event()

        async def _child(**kw):
            started.append(kw["child_sid"])
            await gate.wait()
            return orch.SubagentResult("done", "legal", text="ok")

        monkeypatch.setattr(orch, "_run_child_turn", _child)
        stop = threading.Event()
        first = asyncio.create_task(_call(runner_env))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(_call(runner_env, stop_event=stop))
        await asyncio.sleep(0.01)
        stop.set()
        gate.set()
        return await first, await second

    first, second = _run(go())
    assert first.ok
    assert second.status == "cancelled"
    assert len(started) == 1


# ---------------------------------------------------------------------------
# Child turn: events, timeout and stop
# ---------------------------------------------------------------------------


class _FakePipeline:
    """Stand-in for ``AgentPipeline``: yields scripted chunks.

    ``mode`` selects the behaviour: ``ok`` finishes with one tool step,
    ``hang`` loops until the cancel key is set (then persists a partial
    answer), ``stubborn`` ignores the cancel key so the runner must
    hard-cancel it after the grace period.
    """

    mode = "ok"
    cancelled = False
    seen_source: List[str] = []
    seen_metadata: List[Dict[str, Any]] = []

    def __init__(self, **kw: Any) -> None:
        self.kw = kw

    async def run_stream(self, task: str, **kw: Any):
        _FakePipeline.seen_source.append(kw.get("message_source"))
        _FakePipeline.seen_metadata.append(dict(kw.get("metadata") or {}))
        if self.mode == "empty":
            # What the real pipeline does when the LLM wrote nothing: a
            # turn_outcome warning, then the same warning as final text.
            warn = "Il turno è terminato senza testo di risposta in chat."
            yield {"type": "turn_outcome", "code": "empty_final", "message": warn, "details": {}}
            yield {"type": "final", "text": warn}
            return
        if self.mode == "ok":
            yield {"type": "tool_event", "event": {"type": "tool_start", "id": "t1", "name": "sql_query"}}
            yield {"type": "token", "content": "partial"}
            yield {"type": "tool_event", "event": {"type": "tool_end", "id": "t1", "name": "sql_query"}}
            yield {"type": "final", "text": "Final answer\nwith lines"}
            return
        while True:
            await asyncio.sleep(0.05)
            if self.mode == "hang" and _FakePipeline.cancelled:
                yield {"type": "final", "text": "partial result"}
                return


@pytest.fixture()
def child_env(runner_env, monkeypatch):
    """Patch every dependency of ``_run_child_turn`` and capture parent events."""
    import src.agent_pipeline as ap
    import src.main as main
    import src.runtime.redis_client as rc
    import src.session_workspace as sw

    events: List[Dict[str, Any]] = []
    released: List[str] = []
    _FakePipeline.mode = "ok"
    _FakePipeline.cancelled = False
    _FakePipeline.seen_source = []
    _FakePipeline.seen_metadata = []
    agent_calls: List[Dict[str, Any]] = []

    async def _noop(*a, **kw):
        return None

    async def _agent(slug, **kw):
        assert kw["message_source"] == "subagent" and kw["agent_mode"] == "normal"
        agent_calls.append(kw)
        return object(), slug

    async def _cancel(conversation_id, *a, **kw):
        _FakePipeline.cancelled = True

    async def _runtime(slug, user_id, timeout):
        return {}

    async def _release(child_sid):
        released.append(child_sid)

    monkeypatch.setattr(orch, "_create_child_conversation", _noop)
    monkeypatch.setattr(orch, "_child_runtime", _runtime)
    monkeypatch.setattr(orch, "_release_child_resources", _release)
    monkeypatch.setattr(orch, "_emit_parent", lambda sid, payload: events.append(payload))
    monkeypatch.setattr(main, "get_agent", _agent)
    monkeypatch.setattr(main, "set_event_loop", lambda loop: None)
    monkeypatch.setattr(ap, "AgentPipeline", _FakePipeline)
    monkeypatch.setattr(rc, "redis_set_stream_active", _noop)
    monkeypatch.setattr(rc, "redis_clear_stream_active", _noop)
    monkeypatch.setattr(rc, "redis_set_stream_cancel", _cancel)
    monkeypatch.setattr(sw, "ensure_session_dirs", lambda sid: None)
    monkeypatch.setattr(sw, "sync_parent_uploads_to_child", lambda p, c: {"ok": True})
    monkeypatch.setattr(
        sw,
        "sync_child_outputs_to_parent",
        lambda c, p: {"copied": [f"workspace/subagents/{c}/report.md"]},
    )
    return SimpleNamespace(
        events=events, released=released, profiles=runner_env, agent_calls=agent_calls
    )


def test_child_turn_success_events_and_result(child_env):
    """A finished child returns its final text and files and mirrors steps to the parent."""
    res = _run(_call(child_env.profiles, parent_tool_call_id="call-1", child_session_id="sub_legal_aaa"))
    assert res.ok
    assert res.text == "Final answer\nwith lines"
    assert res.files == ["workspace/subagents/sub_legal_aaa/report.md"]
    assert _FakePipeline.seen_source == ["subagent"]
    types = [e["type"] for e in child_env.events]
    assert types == ["subagent_start", "subagent_step", "subagent_step", "subagent_end"]
    for e in child_env.events:
        assert e["parent_tool_call_id"] == "call-1"
        assert e["child_conversation_id"] == "sub_legal_aaa"
        assert e["profile"] == "legal"
    assert child_env.events[1]["step"] == {"id": "t1", "name": "sql_query", "status": "running"}
    end = child_env.events[-1]
    assert end["status"] == "done" and end["steps"] == 1
    assert end["files"] == res.files
    assert child_env.released == ["sub_legal_aaa"]


def test_child_uses_parent_llm_provider(child_env):
    """The child agent and pipeline get the parent chat's LLM provider, not the env default."""
    res = _run(_call(child_env.profiles, llm_provider_name="prov-x"))
    assert res.ok
    assert child_env.agent_calls[0]["llm_provider_name"] == "prov-x"
    assert _FakePipeline.seen_metadata[0]["llm_provider_name"] == "prov-x"

    # No provider on the parent (env config) -> none forced on the child either.
    _run(_call(child_env.profiles))
    assert child_env.agent_calls[1]["llm_provider_name"] is None
    assert "llm_provider_name" not in _FakePipeline.seen_metadata[1]


def test_child_without_answer_is_an_error(child_env):
    """A child ending with only the pipeline's empty-answer warning is reported as error."""
    _FakePipeline.mode = "empty"
    res = _run(_call(child_env.profiles))
    assert res.status == "error"
    assert "ended without an answer" in res.error
    assert child_env.events[-1]["status"] == "error"


def test_delegate_tool_forwards_llm_provider(profiles, monkeypatch):
    """The tool built for a parent agent passes its provider to every run_subagent call."""
    import src.main as main
    import src.runtime.subagent_orchestrator as so
    from src.runtime.native_tools.factory_table import build_delegate_to_agent_tool

    seen: Dict[str, Any] = {}

    async def _fake_run(**kw):
        seen.update(kw)
        return orch.SubagentResult("done", kw["agent"], text="ok")

    monkeypatch.setattr(so, "run_subagent", _fake_run)

    async def go():
        monkeypatch.setattr(main, "_GLOBAL_LOOP", asyncio.get_running_loop())
        tool = build_delegate_to_agent_tool(
            "conv-1", "alice", profiles["aion_std"], llm_provider_name="prov-x"
        )
        return await asyncio.to_thread(tool.function, agent="legal", task="T")

    out = _run(go())
    assert "status=done" in out
    assert seen["llm_provider_name"] == "prov-x"


def test_child_turn_timeout_soft_cancel(child_env, monkeypatch):
    """On timeout the child gets the cancel key, keeps its partial answer, status=timeout."""
    monkeypatch.setattr(orch, "subagent_timeout_sec", lambda: 0.2)
    _FakePipeline.mode = "hang"
    res = _run(_call(child_env.profiles, child_session_id="sub_legal_bbb"))
    assert res.status == "timeout"
    assert res.text == "partial result"
    assert child_env.events[-1]["status"] == "timeout"
    assert child_env.released == ["sub_legal_bbb"]


def test_child_turn_parent_stop_hard_cancel(child_env, monkeypatch):
    """A parent stop cancels the child; a child ignoring the key is hard-cancelled."""
    monkeypatch.setattr(orch, "_CANCEL_GRACE_SEC", 0.2)
    _FakePipeline.mode = "stubborn"
    stop = threading.Event()

    async def go():
        task = asyncio.create_task(_call(child_env.profiles, stop_event=stop))
        await asyncio.sleep(0.1)
        stop.set()
        return await asyncio.wait_for(task, timeout=5)

    res = _run(go())
    assert res.status == "cancelled"
    assert res.error == "stopped by the user"
    assert _FakePipeline.cancelled is True
    assert child_env.events[-1]["type"] == "subagent_end"
    assert len(child_env.released) == 1


# ---------------------------------------------------------------------------
# Timeline persistence
# ---------------------------------------------------------------------------


def test_timeline_merges_subagent_events_into_tool_segment():
    """``subagent_*`` events land on the delegate_to_agent tool segment and survive tool_end."""
    tb = TimelineBuilder()
    ids = {"parent_tool_call_id": "call-1", "run_id": "r1", "child_conversation_id": "sub_legal_x", "profile": "legal"}

    def ev(payload):
        tb.apply_chunk({"type": "tool_event", "event": payload})

    ev({"type": "tool_start", "id": "call-1", "name": "delegate_to_agent", "input": {"agent": "legal", "task": "T"}})
    ev({"type": "subagent_start", **ids, "profile_name": "Legal", "task": "T"})
    ev({"type": "subagent_step", **ids, "step": {"id": "s1", "name": "read_file", "status": "running"}})
    ev({"type": "subagent_step", **ids, "step": {"id": "s2", "name": "search", "status": "running"}})
    ev({"type": "subagent_step", **ids, "step": {"id": "s1", "name": "read_file", "status": "done"}})
    ev({"type": "subagent_end", **ids, "status": "done", "steps": 2, "error": None, "result_preview": "OK", "files": ["a.md"]})
    ev({"type": "tool_end", "id": "call-1", "name": "delegate_to_agent", "input": {}, "output": "done"})

    tools = [s for s in tb.to_list() if s.get("kind") == "tool"]
    assert len(tools) == 1
    sub = tools[0]["subagent"]
    assert sub["conversationId"] == "sub_legal_x"
    assert sub["profileName"] == "Legal"
    assert sub["status"] == "done"
    assert sub["stepCount"] == 2
    # The step still running at the end is closed with the final status.
    assert [(s["id"], s["status"]) for s in sub["steps"]] == [("s1", "done"), ("s2", "done")]
    assert sub["resultPreview"] == "OK" and sub["files"] == ["a.md"]


def test_timeline_caps_subagent_steps():
    """Only the last SUBAGENT_MAX_STEPS steps are persisted; the counter keeps the total."""
    from src.runtime.timeline_builder import SUBAGENT_MAX_STEPS

    tb = TimelineBuilder()
    ids = {"parent_tool_call_id": "c", "run_id": "r", "child_conversation_id": "sub_x_y", "profile": "x"}
    tb.apply_chunk({"type": "tool_event", "event": {"type": "tool_start", "id": "c", "name": "delegate_to_agent", "input": {}}})
    for i in range(SUBAGENT_MAX_STEPS + 10):
        tb.apply_chunk(
            {"type": "tool_event", "event": {"type": "subagent_step", **ids, "step": {"id": f"s{i}", "name": "t", "status": "running"}}}
        )
    sub = [s for s in tb.to_list() if s.get("kind") == "tool"][0]["subagent"]
    assert len(sub["steps"]) == SUBAGENT_MAX_STEPS
    assert sub["stepCount"] == SUBAGENT_MAX_STEPS + 10
    assert sub["steps"][-1]["id"] == f"s{SUBAGENT_MAX_STEPS + 9}"


# ---------------------------------------------------------------------------
# Child conversation: hidden and read-only
# ---------------------------------------------------------------------------


async def _reset_unified_db(monkeypatch, tmp_path: Path) -> None:
    """Point the unified DB at a fresh SQLite file (same pattern as the branching tests)."""
    import src.data.engine as engine

    if engine._engine is not None:
        await engine._engine.dispose()
    engine._engine = None
    engine._session_factory = None
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    monkeypatch.setenv("AION_DEFAULT_TENANT_ID", "default")
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'aion.db'}")
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AION_CHAT_UI_INTERNAL_SECRET", raising=False)
    from src.settings import get_settings

    get_settings.cache_clear()


def test_child_conversation_hidden_and_read_only(monkeypatch, tmp_path):
    """The child row links to its parent, is hidden from the sidebar and refuses writes (409)."""
    from src.api.chat_ui import get_conversation_chat_ui, list_conversations_chat_ui
    from src.api.ownership import (
        SUBAGENT_READ_ONLY_DETAIL,
        claim_conversation_for_write,
        is_subagent_conversation,
    )
    from src.data.history_bridge import UnifiedHistoryBridge

    async def run():
        await _reset_unified_db(monkeypatch, tmp_path)
        await UnifiedHistoryBridge().add_message("conv-parent", "user", "hi", user_id="alice", message_id="m1")
        await orch._create_child_conversation(
            child_session_id="sub_legal_0123456789ab",
            parent_session_id="conv-parent",
            parent_tool_call_id="call-1",
            user_id="alice",
            tenant_id="default",
            profile_slug="legal",
            profile_name="Legal",
            task="Review clause 4.",
        )

        listed = await list_conversations_chat_ui(user_id="alice")
        ids = [c["id"] for c in listed["data"]]
        assert "conv-parent" in ids
        assert "sub_legal_0123456789ab" not in ids

        details = await get_conversation_chat_ui("sub_legal_0123456789ab", user_id="alice")
        assert details["spawned_by_conversation_id"] == "conv-parent"
        assert details["read_only"] is True

        assert await is_subagent_conversation("sub_legal_0123456789ab")
        assert not await is_subagent_conversation("conv-parent")
        # A normal chat whose id merely starts with sub_ is not read-only.
        assert not await is_subagent_conversation("sub_not_a_child")

        with pytest.raises(HTTPException) as exc:
            await claim_conversation_for_write("sub_legal_0123456789ab", "alice")
        assert exc.value.status_code == 409
        assert exc.value.detail == SUBAGENT_READ_ONLY_DETAIL
        assert await claim_conversation_for_write("conv-parent", "alice") is True

    _run(run())


# ---------------------------------------------------------------------------
# Profile schema and agent modes
# ---------------------------------------------------------------------------


def test_normalize_subagent_slugs_dedup_and_self():
    """The allowlist is lower-cased, de-duplicated and never contains the profile itself."""
    from src.runtime.profile_schema import normalize_subagent_slugs

    assert normalize_subagent_slugs(["Legal", "legal", "SQL", "aion_std", ""], self_slug="aion_std") == [
        "legal",
        "sql",
    ]
    assert normalize_subagent_slugs(None) == []


def test_retired_aion_subagents_mcp_dropped():
    """A leftover ``aion_subagents`` MCP entry in a profile is ignored on load."""
    from src.runtime.profile_schema import _drop_retired_mcp_servers

    assert _drop_retired_mcp_servers(["session_sandbox", "aion_subagents"], "aion_std") == ["session_sandbox"]


def test_delegate_always_blocked_in_plan_and_deep_research(monkeypatch):
    """Env overrides of the blocked lists (old .env files) cannot re-enable delegation."""
    from src.runtime.deep_research_mode import deep_research_blocked_tool_names
    from src.runtime.plan_mode import plan_mode_blocked_tool_names

    monkeypatch.delenv("AION_PLAN_MODE_BLOCKED_TOOLS", raising=False)
    monkeypatch.delenv("AION_DEEP_RESEARCH_BLOCKED_TOOLS", raising=False)
    assert "delegate_to_agent" in plan_mode_blocked_tool_names()
    assert "delegate_to_agent" in deep_research_blocked_tool_names()

    monkeypatch.setenv("AION_PLAN_MODE_BLOCKED_TOOLS", "delegate_to_subagent,skill_view")
    monkeypatch.setenv("AION_DEEP_RESEARCH_BLOCKED_TOOLS", "web_search")
    assert "delegate_to_agent" in plan_mode_blocked_tool_names()
    assert "delegate_to_agent" in deep_research_blocked_tool_names()


def test_subagent_source_forces_normal_mode():
    """Sub-agent turns always run in normal mode, whatever mode the caller asks for."""
    from src.runtime.agent_mode_resolve import resolve_agent_mode

    assert resolve_agent_mode("plan", plan_mode=True, message_source="subagent") == "normal"
    assert (
        resolve_agent_mode("deep_research", deep_research_mode=True, message_source="subagent")
        == "normal"
    )
    # Control: a normal user message keeps the requested mode.
    assert resolve_agent_mode("plan", plan_mode=True) == "plan"
