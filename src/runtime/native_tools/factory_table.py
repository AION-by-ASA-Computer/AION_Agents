"""Factory registrate per tool nativi (id logico → costruttore Haystack Tool)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, Dict, Optional

from haystack.tools import Tool

if TYPE_CHECKING:
    from src.agent_profile import AgentProfile

from src.runtime.context import get_context, get_current_session_id
from src.runtime.native_tool_events import (
    emit_tool_end,
    emit_tool_error,
    emit_tool_start,
)
from src.runtime.turn_compaction import maybe_compact_after_tool
from src.runtime.web_search_context import get_web_search_request_context

from .web_providers import run_web_fetch_page, run_web_search

NativeToolBuilder = Callable[..., Tool]


class WebSearchExecutor:
    def __call__(
        self,
        query: str,
        max_results: int | None = None,
        language: str | None = None,
    ) -> str:
        sid = get_current_session_id()
        inp = {"query": query, "max_results": max_results, "language": language}
        from src.runtime.plan_engine import apply_plan_mode_research_gate

        blocked = apply_plan_mode_research_gate(
            "web_search", session_id=sid, tool_input=inp
        )
        if blocked:
            return json.dumps(
                {"error": "plan_research_budget", "message": blocked, "results": []},
                ensure_ascii=False,
            )
        call_id = emit_tool_start(sid, "web_search", inp)
        try:
            fwd = get_context()
            ws_enabled = fwd.get("web_search_enabled")
            if ws_enabled is None:
                ws_enabled = get_web_search_request_context().enabled
            elif not isinstance(ws_enabled, bool):
                ws_enabled = bool(ws_enabled)
            if not ws_enabled:
                out = json.dumps(
                    {
                        "error": "web_search_disabled_by_user",
                        "message": "Web search disabled for this message.",
                        "results": [],
                    },
                    ensure_ascii=False,
                )
            else:
                out = run_web_search(query, max_results=max_results, language=language)
        except Exception as e:
            emit_tool_error(sid, "web_search", call_id, str(e))
            raise
        emit_tool_end(sid, "web_search", call_id, out)
        return maybe_compact_after_tool(
            tool_name="web_search", result=out, arguments=inp
        )


class WebFetchPageExecutor:
    def __call__(self, url: str, prefer_stealth: bool = False) -> str:
        sid = get_current_session_id()
        inp = {"url": url, "prefer_stealth": prefer_stealth}
        from src.runtime.plan_engine import apply_plan_mode_research_gate

        blocked = apply_plan_mode_research_gate(
            "web_fetch_page", session_id=sid, tool_input=inp
        )
        if blocked:
            return json.dumps(
                {
                    "error": "plan_research_budget",
                    "message": blocked,
                    "url": url,
                    "text": "",
                },
                ensure_ascii=False,
            )
        call_id = emit_tool_start(sid, "web_fetch_page", inp)
        try:
            fwd = get_context()
            ws_enabled = fwd.get("web_search_enabled")
            if ws_enabled is None:
                ws_enabled = get_web_search_request_context().enabled
            elif not isinstance(ws_enabled, bool):
                ws_enabled = bool(ws_enabled)
            if not ws_enabled:
                out = json.dumps(
                    {
                        "error": "web_search_disabled_by_user",
                        "message": "Page download disabled together with web search.",
                        "url": url,
                        "text": "",
                    },
                    ensure_ascii=False,
                )
            else:
                out = run_web_fetch_page(url, prefer_stealth=prefer_stealth)
        except Exception as e:
            emit_tool_error(sid, "web_fetch_page", call_id, str(e))
            raise
        emit_tool_end(sid, "web_fetch_page", call_id, out)
        return maybe_compact_after_tool(
            tool_name="web_fetch_page", result=out, arguments=inp
        )


def _profile_slug(profile: Optional["AgentProfile"]) -> str:
    if profile is None:
        return "default"
    return str(getattr(profile, "slug", None) or getattr(profile, "name", "default"))


def build_web_search_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    _ = session_id, user_id, profile
    return Tool(
        name="web_search",
        description=(
            "Web search (Tavily / Brave / SearXNG). Returns TOON or JSON with "
            "results[{title,url,snippet,provider}]. Use short natural-language queries; "
            "prefer fetching a known URL with web_fetch_page over repeated searches."
        ),
        function=WebSearchExecutor(),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max results (1–20, default 8 from AION_WEB_SEARCH_MAX_RESULTS)",
                },
                "language": {
                    "type": "string",
                    "description": "Optional language code (es. it, en)",
                },
            },
            "required": ["query"],
        },
    )


def build_web_fetch_page_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    _ = session_id, user_id, profile
    return Tool(
        name="web_fetch_page",
        description=(
            "Scarica il contenuto testuale di una singola pagina HTTP(S) "
            "(Scrapling Fetcher, fallback httpx + trafilatura/bs4). "
            "Output grandi vanno su disco con AION_TOOL_OFFLOAD_ENABLED. "
            "Returns TOON or JSON with text."
        ),
        function=WebFetchPageExecutor(),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL completo https://..."},
                "prefer_stealth": {
                    "type": "boolean",
                    "description": "Se true e AION_SCRAPLING_STEALTH_ENABLED=1, tenta browser stealth (lento)",
                },
            },
            "required": ["url"],
        },
    )


def _build_sql_memory_tool(
    tool_id: str,
    session_id: str,
    user_id: str,
    profile: Optional["AgentProfile"] = None,
) -> Tool:
    from src.runtime.sql_query_memory_tools import build_sql_query_memory_haystack_tools

    slug = _profile_slug(profile)
    for t in build_sql_query_memory_haystack_tools(session_id, user_id, slug):
        if getattr(t, "name", None) == tool_id:
            return t
    raise ValueError(
        f"SQL QueryMemory tool {tool_id!r} not built (disabled or profile mismatch)"
    )


def build_sql_memory_search_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool("sql_memory_search", session_id, user_id, profile)


def build_sql_memory_save_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool("sql_memory_save", session_id, user_id, profile)


def build_sql_memory_update_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool("sql_memory_update", session_id, user_id, profile)


def build_sql_memory_delete_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool("sql_memory_delete", session_id, user_id, profile)


def build_sql_memory_list_projects_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool(
        "sql_memory_list_projects", session_id, user_id, profile
    )


def build_sql_memory_list_saved_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    return _build_sql_memory_tool("sql_memory_list_saved", session_id, user_id, profile)


def build_memory_recall_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    from src.runtime.mnemos_tools import build_memory_recall_tool as _b

    return _b(session_id, user_id, profile)


def build_memory_note_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    from src.runtime.mnemos_tools import build_memory_note_tool as _b

    return _b(session_id, user_id, profile)


def build_memory_forget_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    from src.runtime.mnemos_tools import build_memory_forget_tool as _b

    return _b(session_id, user_id, profile)


def build_trigger_research_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    _ = profile
    from src.research.handler import deep_research_enabled

    if not deep_research_enabled():
        raise ValueError("trigger_research disabled (AION_DEEP_RESEARCH_ENABLED=0)")

    owner_id = (user_id or "default").strip() or "default"

    def trigger_fn(
        topic: str,
        max_rounds: int | None = None,
        max_time: int | None = None,
        category: str | None = None,
    ) -> str:
        sid = session_id or get_current_session_id()
        inp: dict = {"topic": topic}
        if max_rounds is not None:
            inp["max_rounds"] = max_rounds
        if max_time is not None:
            inp["max_time"] = max_time
        if category:
            inp["category"] = category
        call_id = emit_tool_start(sid, "trigger_research", inp)
        try:
            from src.runtime.native_tools.research_tools import run_trigger_research

            out = run_trigger_research(
                json.dumps(inp), session_id=sid, user_id=owner_id
            )
        except Exception as e:
            emit_tool_error(sid, "trigger_research", call_id, str(e))
            raise
        emit_tool_end(sid, "trigger_research", call_id, out)
        return out

    return Tool(
        name="trigger_research",
        description=(
            "Start a deep research job on any topic — streams progress and produces a detailed "
            "HTML report with export-to-PDF. Use for 'research X', 'deep research on Y', "
            "'investigate Z'. NOT for quick facts (use web_search in normal mode)."
        ),
        function=trigger_fn,
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Research question or topic",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "Optional max search rounds (0=auto)",
                },
                "max_time": {
                    "type": "integer",
                    "description": "Optional max seconds (60-1800)",
                },
                "category": {
                    "type": "string",
                    "description": "Optional: product, comparison, howto, factcheck",
                },
            },
            "required": ["topic"],
        },
    )


def build_manage_research_tool(
    session_id: str, user_id: str, profile: Optional["AgentProfile"] = None
) -> Tool:
    _ = session_id, profile
    from src.research.handler import deep_research_enabled

    if not deep_research_enabled():
        raise ValueError("manage_research disabled (AION_DEEP_RESEARCH_ENABLED=0)")

    owner_id = (user_id or "default").strip() or "default"

    def manage_fn(
        action: str = "list", id: str | None = None, search: str | None = None
    ) -> str:
        sid = get_current_session_id()
        inp = {"action": action}
        if id:
            inp["id"] = id
        if search:
            inp["search"] = search
        call_id = emit_tool_start(sid, "manage_research", inp)
        try:
            from src.runtime.native_tools.research_tools import run_manage_research

            out = run_manage_research(json.dumps(inp), user_id=owner_id)
        except Exception as e:
            emit_tool_error(sid, "manage_research", call_id, str(e))
            raise
        emit_tool_end(sid, "manage_research", call_id, out)
        return out

    return Tool(
        name="manage_research",
        description=(
            "List, read, or delete saved deep research reports. "
            "action=list|read|delete; id required for read/delete."
        ),
        function=manage_fn,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read", "delete"]},
                "id": {"type": "string", "description": "Research session id"},
                "search": {
                    "type": "string",
                    "description": "Filter library by query text",
                },
            },
            "required": ["action"],
        },
    )


def build_delegate_to_agent_tool(
    session_id: str,
    user_id: str,
    profile: Optional["AgentProfile"] = None,
    *,
    llm_provider_name: Optional[str] = None,
) -> Tool:
    """Build ``delegate_to_agent``: hand a self-contained task to a sub-agent profile.

    Injected by ``src.main.build_all_tools`` only for profiles with a
    ``subagents`` allowlist and never in child sessions (depth 1). The ``agent``
    parameter is an enum of the allowlisted profiles and the description lists
    each one with its profile description, so the LLM can pick the right
    specialist. Runs in a ToolInvoker worker thread: the actual work is the
    coroutine ``run_subagent`` scheduled on the main event loop; tool calls of
    the same step run in parallel (capped by ``AION_SUBAGENT_MAX_PARALLEL``).

    ``llm_provider_name`` is the LLM provider (DB slug) of the parent chat. The
    parent agent is cached per provider, so capturing it here is safe; every
    child runs on the same provider/model the user picked for the main chat
    (``None`` = env configuration, exactly like the parent).

    Raises ``ValueError`` when no allowlisted profile exists, so the caller can
    skip the tool. See docs/agents/subagents.md.
    """
    from src.runtime.subagent_orchestrator import (
        DELEGATE_TOOL_NAME,
        SubagentResult,
        run_subagent,
        subagent_catalog,
        subagent_timeout_sec,
    )

    catalog = subagent_catalog(profile)
    if not catalog:
        raise ValueError("no sub-agent profile available")
    owner_id = (user_id or "default").strip() or "default"

    lines = []
    for item in catalog:
        desc = " ".join(item["description"].split())[:300] or "(no description)"
        lines.append(f"- {item['slug']} ({item['name']}): {desc}")
    description = (
        "Delegate a self-contained task to a specialised sub-agent and get its "
        "final answer back. The sub-agent runs in its own isolated session with "
        "its own tools and skills: it does NOT see this conversation, so `task` "
        "must contain every piece of context, data, file name and constraint it "
        "needs, plus the expected output format. Files uploaded in this chat are "
        "available to it; files it produces are copied into this session. Use it "
        "for work that matches a sub-agent's specialty or that can run in "
        "parallel (call it several times in the same step for independent "
        "tasks). Do not delegate trivial steps you can do directly.\n"
        "Available sub-agents:\n" + "\n".join(lines)
    )

    def delegate_fn(agent: str, task: str) -> str:
        import asyncio
        import concurrent.futures

        # Captured in the tool thread: session, loop and the parent turn's
        # stop_event are only reachable through the forwarded context.
        ctx = get_context()
        sid = ctx.get("session_id") or session_id
        stop_event = ctx.get("stop_event")
        inp = {"agent": agent, "task": task}
        call_id = emit_tool_start(sid, DELEGATE_TOOL_NAME, inp)

        from src.main import _GLOBAL_LOOP

        loop = _GLOBAL_LOOP or ctx.get("loop")
        if loop is None:
            err = "no event loop available for sub-agent execution"
            emit_tool_error(sid, DELEGATE_TOOL_NAME, call_id, err)
            return SubagentResult("error", agent, error=err).to_tool_output()

        fut = asyncio.run_coroutine_threadsafe(
            run_subagent(
                parent_session_id=sid,
                parent_profile=profile,
                user_id=owner_id,
                agent=agent,
                task=task,
                parent_tool_call_id=call_id,
                stop_event=stop_event,
                llm_provider_name=llm_provider_name,
            ),
            loop,
        )
        # The runner enforces its own timeout and cancel grace; this outer
        # bound only protects the worker thread if the loop is wedged. The
        # extra margin covers the wait for a parallelism slot.
        outer_timeout = subagent_timeout_sec() * 2 + 60
        try:
            result = fut.result(timeout=outer_timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            result = SubagentResult("timeout", agent, error="sub-agent did not finish")
        except Exception as e:
            result = SubagentResult("error", agent, error=str(e))

        out = result.to_tool_output()
        if result.ok:
            emit_tool_end(sid, DELEGATE_TOOL_NAME, call_id, out)
        else:
            emit_tool_error(sid, DELEGATE_TOOL_NAME, call_id, result.error or result.status)
        return out

    return Tool(
        name=DELEGATE_TOOL_NAME,
        description=description,
        function=delegate_fn,
        parameters={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": [c["slug"] for c in catalog],
                    "description": "Slug of the sub-agent profile to run.",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Complete, self-contained instructions for the sub-agent: "
                        "goal, all needed context and data, constraints, expected "
                        "output format."
                    ),
                },
            },
            "required": ["agent", "task"],
        },
    )


NATIVE_TOOL_FACTORIES: Dict[str, NativeToolBuilder] = {
    "web_search": build_web_search_tool,
    "web_fetch_page": build_web_fetch_page_tool,
    "sql_memory_search": build_sql_memory_search_tool,
    "sql_memory_save": build_sql_memory_save_tool,
    "sql_memory_update": build_sql_memory_update_tool,
    "sql_memory_delete": build_sql_memory_delete_tool,
    "sql_memory_list_projects": build_sql_memory_list_projects_tool,
    "sql_memory_list_saved": build_sql_memory_list_saved_tool,
    "memory_recall": build_memory_recall_tool,
    "memory_note": build_memory_note_tool,
    "memory_forget": build_memory_forget_tool,
    "trigger_research": build_trigger_research_tool,
    "manage_research": build_manage_research_tool,
}
