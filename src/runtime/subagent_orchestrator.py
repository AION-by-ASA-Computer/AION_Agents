"""Sub-agent runner: executes a task delegated by a parent agent (Claude-style).

The main chat agent calls the native tool ``delegate_to_agent(agent, task)``
(built in ``src/runtime/native_tools/factory_table.py``). The tool runs in a
ToolInvoker worker thread and schedules :func:`run_subagent` on the main event
loop, which:

1. checks the requested profile against the parent's ``subagents`` allowlist
   intersected with the profiles the user may use (``profile_access``);
2. creates a hidden, read-only child conversation (``sub_<slug>_<hex>``) linked
   to the parent via ``conversations.spawned_by_conversation_id``;
3. runs one headless ``AgentPipeline`` turn with ``message_source="subagent"``
   (forced normal mode, no LTM / skill distillation, depth 1: the child never
   gets ``delegate_to_agent``);
4. forwards progress to the **parent** session bus as ``subagent_start`` /
   ``subagent_step`` / ``subagent_end`` events, which the parent stream relays
   to chat-ui (live card) and the timeline builder persists;
5. propagates a parent stop to the child (cancel key), enforces a timeout and a
   per-parent-session parallelism cap, then releases the child's MCP workers,
   deferred tool pools and cached agents.

See docs/agents/subagents.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.identity import sanitize_user_id
from src.runtime.tool_events import tool_event_bus

logger = logging.getLogger("aion.subagent")

# Prefix of every child conversation id. Also used to enforce depth 1: a
# session whose id starts with it never receives ``delegate_to_agent``.
SUBAGENT_SESSION_PREFIX = "sub_"

# Value of ``message_source`` / ``metadata.source`` for sub-agent turns.
SUBAGENT_MESSAGE_SOURCE = "subagent"

# Name of the native tool exposed to the parent agent.
DELEGATE_TOOL_NAME = "delegate_to_agent"

# Grace period between the cancel signal and the hard cancellation of the
# child turn: lets the pipeline persist what it produced and close cleanly.
_CANCEL_GRACE_SEC = 15.0

# Max length of the task stored in the conversation metadata / SSE events
# (the full task is the child's first user message anyway).
_TASK_PREVIEW_CHARS = 2000

_SLUG_SAFE_RE = re.compile(r"[^a-z0-9_-]+")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive integer env var, falling back to ``default`` on junk."""
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def subagent_max_parallel() -> int:
    """Max concurrent sub-agents per parent session (``AION_SUBAGENT_MAX_PARALLEL``).

    Default 3: it stays below the ToolInvoker thread pool (4 workers), so a
    parent step can always run at least one other tool alongside delegations.
    """
    return _env_int("AION_SUBAGENT_MAX_PARALLEL", 3)


def subagent_timeout_sec() -> int:
    """Wall-clock budget of one sub-agent turn (``AION_SUBAGENT_TIMEOUT_SEC``)."""
    return _env_int("AION_SUBAGENT_TIMEOUT_SEC", 300, minimum=10)


def subagent_result_max_chars() -> int:
    """Max chars of the result returned to the parent LLM (``AION_SUBAGENT_RESULT_MAX_CHARS``)."""
    return _env_int("AION_SUBAGENT_RESULT_MAX_CHARS", 8000, minimum=500)


def is_subagent_session(session_id: Optional[str]) -> bool:
    """True for child conversations created by this module (depth-1 guard)."""
    return (session_id or "").startswith(SUBAGENT_SESSION_PREFIX)


def new_child_session_id(slug: str) -> str:
    """Build a child conversation id ``sub_<slug>_<12 hex>`` (≤ 64 chars, path-safe)."""
    safe = _SLUG_SAFE_RE.sub("-", (slug or "agent").lower()).strip("-")[:40] or "agent"
    return f"{SUBAGENT_SESSION_PREFIX}{safe}_{uuid.uuid4().hex[:12]}"


def truncate_result(text: str, max_chars: Optional[int] = None) -> str:
    """Shorten a sub-agent answer for the parent context, keeping line breaks.

    Keeps the head (most of the budget) and the tail (conclusions often sit at
    the end) with an explicit marker in between. Unlike
    ``src.a2a.context_distill.distill_subagent_output`` it never collapses
    newlines, so tables, lists and code blocks survive.
    """
    limit = max_chars if max_chars is not None else subagent_result_max_chars()
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    marker = f"\n\n… [{len(t) - limit} characters omitted — full answer in the sub-agent transcript] …\n\n"
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    return t[:head].rstrip() + marker + t[-tail:].lstrip()


# ---------------------------------------------------------------------------
# Catalog / access control
# ---------------------------------------------------------------------------


def subagent_catalog(parent_profile: Any) -> List[Dict[str, str]]:
    """Profiles listed in ``parent_profile.subagents`` that actually exist.

    Returns ``[{"slug", "name", "description"}]`` in allowlist order. Used to
    build the tool schema (enum + description) shown to the parent LLM.
    Missing profiles are skipped (the admin may have deleted one).
    """
    from src.agent_profile import profile_manager

    out: List[Dict[str, str]] = []
    for slug in list(getattr(parent_profile, "subagents", None) or []):
        prof = profile_manager.get_profile(slug)
        # get_profile may fall back to a default profile: require an exact match.
        if not prof or getattr(prof, "slug", "") != slug:
            continue
        out.append(
            {
                "slug": slug,
                "name": str(getattr(prof, "name", "") or slug),
                "description": str(getattr(prof, "description", "") or "").strip(),
            }
        )
    return out


async def allowed_subagent_slugs(
    parent_profile: Any, user_id: str, tenant_id: str = "default"
) -> List[str]:
    """Allowlist of the parent profile ∩ profiles the user may use.

    ``allowed_profile_slugs`` returns an empty list when the user has no
    per-profile restriction, in which case the parent allowlist applies as is.
    """
    catalog = [c["slug"] for c in subagent_catalog(parent_profile)]
    if not catalog:
        return []
    try:
        from src.api.profile_access import allowed_profile_slugs, user_row_id_for

        allowed = await allowed_profile_slugs(await user_row_id_for(user_id, tenant_id))
    except Exception as e:
        # Fail closed: without the access list we cannot prove the user may
        # use the sub-agent profile.
        logger.warning("subagent access check failed user=%s: %s", user_id, e)
        return []
    if not allowed:
        return catalog
    allowed_set = {s.lower() for s in allowed}
    return [s for s in catalog if s in allowed_set]


# ---------------------------------------------------------------------------
# Parallelism cap (per parent session)
# ---------------------------------------------------------------------------

_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}
_SEM_USERS: Dict[str, int] = {}


class _ParentSlot:
    """Async context manager limiting concurrent sub-agents of one parent session.

    Semaphores are created lazily on the running loop and dropped when the
    last user releases them, so idle sessions do not leak entries.
    """

    def __init__(self, parent_session_id: str) -> None:
        self.key = parent_session_id

    async def __aenter__(self) -> None:
        sem = _SEMAPHORES.get(self.key)
        if sem is None:
            sem = asyncio.Semaphore(subagent_max_parallel())
            _SEMAPHORES[self.key] = sem
        _SEM_USERS[self.key] = _SEM_USERS.get(self.key, 0) + 1
        try:
            await sem.acquire()
        except BaseException:
            self._drop_user()
            raise

    async def __aexit__(self, *exc: Any) -> None:
        sem = _SEMAPHORES.get(self.key)
        if sem is not None:
            sem.release()
        self._drop_user()

    def _drop_user(self) -> None:
        left = _SEM_USERS.get(self.key, 1) - 1
        if left <= 0:
            _SEM_USERS.pop(self.key, None)
            _SEMAPHORES.pop(self.key, None)
        else:
            _SEM_USERS[self.key] = left


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SubagentResult:
    """Outcome of one delegation, rendered for the parent LLM by :meth:`to_tool_output`.

    ``status`` is one of ``done`` | ``error`` | ``cancelled`` | ``timeout`` |
    ``denied``.
    """

    status: str
    agent: str
    child_conversation_id: Optional[str] = None
    text: str = ""
    error: Optional[str] = None
    files: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "done"

    def to_tool_output(self) -> str:
        """Plain-text tool result: a header line, the answer, then produced files.

        Plain text (not JSON) so the answer keeps its markdown layout in the
        parent context instead of becoming one escaped string.
        """
        header = f"[sub-agent {self.agent} · status={self.status}"
        if self.child_conversation_id:
            header += f" · conversation={self.child_conversation_id}"
        header += "]"
        parts = [header]
        if self.error:
            parts.append(f"Error: {self.error}")
        if self.text:
            parts.append(self.text)
        elif self.ok:
            parts.append("(the sub-agent returned no text)")
        if self.files:
            parts.append(
                "Files produced by the sub-agent (copied into this session):\n"
                + "\n".join(f"- {f}" for f in self.files)
            )
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Child conversation bookkeeping
# ---------------------------------------------------------------------------


async def _parent_tenant(parent_session_id: str, user_id: str) -> str:
    """Tenant of the parent conversation (``default`` if it is not persisted yet).

    Also refuses to spawn from a conversation owned by another user, which
    should never happen since the tool is built for the session owner.
    """
    from src.data.engine import get_async_session_maker
    from src.data.models import Conversation

    async with get_async_session_maker()() as session:
        row = await session.get(Conversation, parent_session_id)
    if row is None:
        return "default"
    if sanitize_user_id(row.user_id) != sanitize_user_id(user_id):
        raise PermissionError("session_not_owned")
    return (row.tenant_id or "default").strip() or "default"


async def _create_child_conversation(
    *,
    child_session_id: str,
    parent_session_id: str,
    parent_tool_call_id: Optional[str],
    user_id: str,
    tenant_id: str,
    profile_slug: str,
    profile_name: str,
    task: str,
    llm_provider_name: Optional[str] = None,
) -> None:
    """Insert the hidden child conversation row and claim its owner marker.

    Modelled on ``cron_runner._ensure_conversation``. The row is created before
    the pipeline runs so that ``spawned_by_conversation_id`` (which hides it
    from the sidebar and makes it read-only) is set from the very first write;
    the pipeline's persistence then reuses this row.
    """
    from datetime import datetime, timezone

    from src.api.ownership import claim_session_owner_marker
    from src.data.engine import get_async_session_maker
    from src.data.models import Conversation

    title_task = " ".join(task.split())[:80]
    meta = {
        "source": SUBAGENT_MESSAGE_SOURCE,
        "spawned_by": parent_session_id,
        "parent_tool_call_id": parent_tool_call_id,
        "profile": profile_slug,
        "task": task[:_TASK_PREVIEW_CHARS],
    }
    if llm_provider_name:
        # Same key the chat API stores, so the transcript shows the model used.
        meta["llm_provider_name"] = llm_provider_name
    claim_session_owner_marker(child_session_id, sanitize_user_id(user_id))
    async with get_async_session_maker()() as session:
        session.add(
            Conversation(
                id=child_session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                profile_slug=profile_slug,
                title=f"{profile_name}: {title_task}",
                message_count=0,
                metadata_json=json.dumps(meta, ensure_ascii=False),
                spawned_by_conversation_id=parent_session_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _release_child_resources(child_session_id: str) -> None:
    """Free everything the child turn allocated in-process.

    Child sessions are single-turn: their MCP stdio workers, deferred tool
    pools and cached agents would otherwise linger until LRU eviction.
    """
    try:
        from src.mcp_manager import mcp_manager

        await mcp_manager.release_session(child_session_id)
    except Exception as e:
        logger.debug("subagent release_session %s: %s", child_session_id, e)
    try:
        from src.runtime.tool_exposure import clear_deferred_tools

        clear_deferred_tools(child_session_id)
    except Exception as e:
        logger.debug("subagent clear_deferred_tools %s: %s", child_session_id, e)
    try:
        from src.main import evict_session_agents

        await evict_session_agents(child_session_id)
    except Exception as e:
        logger.debug("subagent evict_session_agents %s: %s", child_session_id, e)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _emit_parent(parent_session_id: str, payload: Dict[str, Any]) -> None:
    """Publish a ``subagent_*`` event on the parent's tool event bus.

    Runs on the event loop thread (the runner is a coroutine there), so the
    bus can be called directly; the parent stream loop wraps the payload into
    a ``tool_event`` SSE chunk.
    """
    try:
        tool_event_bus.put_event(parent_session_id, payload)
    except Exception as e:
        logger.debug("subagent event drop parent=%s: %s", parent_session_id, e)


def _step_from_chunk(evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a child ``tool_event`` payload to a compact step for the parent card.

    Only tool start/end/error are forwarded (no tokens, no reasoning): the card
    shows what the sub-agent is doing, the transcript holds the details.
    """
    etype = evt.get("type")
    if etype not in ("tool_start", "tool_end", "tool_error"):
        return None
    status = {"tool_start": "running", "tool_end": "done", "tool_error": "error"}[etype]
    if etype == "tool_end" and evt.get("error"):
        status = "error"
    return {
        "id": str(evt.get("id") or ""),
        "name": str(evt.get("name") or "tool"),
        "status": status,
    }


async def run_subagent(
    *,
    parent_session_id: str,
    parent_profile: Any,
    user_id: str,
    agent: str,
    task: str,
    parent_tool_call_id: Optional[str] = None,
    child_session_id: Optional[str] = None,
    stop_event: Any = None,
    llm_provider_name: Optional[str] = None,
) -> SubagentResult:
    """Run ``task`` with the ``agent`` profile in a hidden child conversation.

    ``llm_provider_name`` is the LLM provider (DB slug) of the parent chat: the
    child always runs on the same provider/model as the parent. ``None`` means
    the env LLM configuration, exactly as for the parent.

    ``stop_event`` is the parent turn's ``threading.Event``: when it is set the
    child receives a cancel signal (and is hard-cancelled after a grace period).
    ``parent_tool_call_id`` is the id of the parent's ``delegate_to_agent`` tool
    call; every ``subagent_*`` event carries it so chat-ui and the timeline
    builder attach the progress to the right tool card.

    Never raises for expected failures: they come back as a non-``done``
    :class:`SubagentResult` the parent LLM can reason about.
    """
    slug = (agent or "").strip().lower()
    task = (task or "").strip()
    if not task:
        return SubagentResult("error", slug, error="empty task")
    if is_subagent_session(parent_session_id):
        # Depth 1: a sub-agent never delegates (the tool is not even built for
        # it, this is defence in depth).
        return SubagentResult("denied", slug, error="sub-agents cannot delegate")

    try:
        tenant_id = await _parent_tenant(parent_session_id, user_id)
    except PermissionError:
        return SubagentResult("denied", slug, error="session not owned by the user")

    allowed = await allowed_subagent_slugs(parent_profile, user_id, tenant_id)
    if slug not in allowed:
        return SubagentResult(
            "denied",
            slug,
            error=(
                f"agent '{slug}' is not available; allowed: {', '.join(allowed) or 'none'}"
            ),
        )

    from src.agent_profile import profile_manager

    child_profile = profile_manager.get_profile(slug)
    if not child_profile or child_profile.slug != slug:
        return SubagentResult("denied", slug, error=f"profile '{slug}' not found")

    child_sid = child_session_id or new_child_session_id(slug)
    run_id = uuid.uuid4().hex[:12]
    base_evt = {
        "parent_tool_call_id": parent_tool_call_id,
        "run_id": run_id,
        "child_conversation_id": child_sid,
        "profile": slug,
    }

    async with _ParentSlot(parent_session_id):
        # The parent may have been stopped while this call waited for a slot.
        if stop_event is not None and stop_event.is_set():
            return SubagentResult("cancelled", slug, error="parent turn stopped")
        return await _run_child_turn(
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            child_profile=child_profile,
            child_sid=child_sid,
            user_id=user_id,
            tenant_id=tenant_id,
            task=task,
            stop_event=stop_event,
            base_evt=base_evt,
            llm_provider_name=llm_provider_name,
        )


async def _run_child_turn(
    *,
    parent_session_id: str,
    parent_tool_call_id: Optional[str],
    child_profile: Any,
    child_sid: str,
    user_id: str,
    tenant_id: str,
    task: str,
    stop_event: Any,
    base_evt: Dict[str, Any],
    llm_provider_name: Optional[str] = None,
) -> SubagentResult:
    """Create the child conversation, stream its turn and report to the parent.

    Split from :func:`run_subagent` so the parallelism slot wraps exactly the
    part that consumes LLM/tool capacity.
    """
    from src.agent_pipeline import AgentPipeline
    from src.main import get_agent, set_event_loop
    from src.runtime.redis_client import (
        redis_clear_stream_active,
        redis_set_stream_active,
        redis_set_stream_cancel,
    )
    from src.session_workspace import (
        ensure_session_dirs,
        sync_child_outputs_to_parent,
        sync_parent_uploads_to_child,
    )

    slug = child_profile.slug
    profile_name = str(getattr(child_profile, "name", "") or slug)
    loop = asyncio.get_running_loop()
    timeout = float(subagent_timeout_sec())

    try:
        await _create_child_conversation(
            child_session_id=child_sid,
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            user_id=user_id,
            tenant_id=tenant_id,
            profile_slug=slug,
            profile_name=profile_name,
            task=task,
            llm_provider_name=llm_provider_name,
        )
    except Exception as e:
        logger.exception("subagent: cannot create child conversation %s", child_sid)
        return SubagentResult(
            "error", slug, error=f"cannot create sub-agent session: {e}"
        )

    # Give the child the files the user uploaded in the parent conversation.
    ensure_session_dirs(child_sid)
    sync_meta = sync_parent_uploads_to_child(parent_session_id, child_sid)
    if sync_meta.get("errors"):
        logger.warning(
            "subagent upload sync %s → %s: %s", parent_session_id, child_sid, sync_meta
        )

    _emit_parent(
        parent_session_id,
        {
            "type": "subagent_start",
            **base_evt,
            "profile_name": profile_name,
            "task": task[:_TASK_PREVIEW_CHARS],
        },
    )

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    final_text: Optional[str] = None
    tokens: List[str] = []
    child_error: Optional[str] = None
    # Warning of a ``turn_outcome`` chunk: the pipeline emits one when the turn
    # produced no real answer and then uses the warning as the final text.
    outcome_warning: Optional[str] = None
    outcome_code: Optional[str] = None
    steps = 0

    async def _consume() -> None:
        """Drive the child pipeline and mirror its tool steps to the parent."""
        nonlocal final_text, child_error, steps, outcome_warning, outcome_code
        set_event_loop(loop)
        agent_instance, resolved = await get_agent(
            slug,
            session_id=child_sid,
            user_id=user_id,
            tenant_id=tenant_id,
            agent_mode="normal",
            message_source=SUBAGENT_MESSAGE_SOURCE,
            # Without it the child falls back to the env LLM config instead of
            # the provider the user picked for the parent chat.
            llm_provider_name=llm_provider_name,
        )
        pipeline = AgentPipeline(
            agent=agent_instance,
            session_id=child_sid,
            profile_name=resolved,
            user_id=user_id,
            agent_mode="normal",
        )
        runtime = await _child_runtime(slug, user_id, timeout)
        async for chunk in pipeline.run_stream(
            task,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            message_source=SUBAGENT_MESSAGE_SOURCE,
            metadata={
                "source": SUBAGENT_MESSAGE_SOURCE,
                **(
                    {"llm_provider_name": llm_provider_name}
                    if llm_provider_name
                    else {}
                ),
            },
            runtime=runtime,
        ):
            ctype = str(chunk.get("type") or "")
            if ctype == "token":
                tokens.append(str(chunk.get("content") or ""))
            elif ctype == "final":
                final_text = str(chunk.get("text") or "")
            elif ctype == "error":
                child_error = str(chunk.get("content") or "error")
            elif ctype == "turn_outcome":
                outcome_warning = str(chunk.get("message") or "").strip() or None
                outcome_code = str(chunk.get("code") or "").strip() or None
            elif ctype == "tool_event":
                step = _step_from_chunk(chunk.get("event") or {})
                if step:
                    if step["status"] == "running":
                        steps += 1
                    _emit_parent(
                        parent_session_id,
                        {"type": "subagent_step", **base_evt, "step": step},
                    )

    # Mark the child stream active so the read-only transcript can show that
    # the sub-agent is still working (same contract as cron runs).
    try:
        await redis_set_stream_active(
            child_sid,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
            profile_name=slug,
        )
    except Exception:
        pass

    status = "done"
    consume_task = asyncio.create_task(_consume())
    deadline = loop.time() + timeout
    cancel_sent_at: Optional[float] = None
    try:
        # Poll instead of a plain wait_for: the parent's stop_event is a
        # threading.Event set from another thread, it cannot be awaited.
        while not consume_task.done():
            await asyncio.wait({consume_task}, timeout=0.5)
            if consume_task.done():
                break
            now = loop.time()
            if cancel_sent_at is None:
                if stop_event is not None and stop_event.is_set():
                    status = "cancelled"
                elif now >= deadline:
                    status = "timeout"
                if status != "done":
                    # Soft stop first: the child pipeline consumes this key,
                    # stops its agent and persists the partial answer.
                    await redis_set_stream_cancel(child_sid)
                    cancel_sent_at = now
            elif now - cancel_sent_at >= _CANCEL_GRACE_SEC:
                consume_task.cancel()
                break
        try:
            await consume_task
        except asyncio.CancelledError:
            if status == "done":
                raise
        except Exception as e:
            logger.exception("subagent child turn failed %s", child_sid)
            child_error = child_error or str(e)
    except asyncio.CancelledError:
        # The parent tool call itself was cancelled: stop the child too.
        consume_task.cancel()
        status = "cancelled"
        raise
    finally:
        try:
            await redis_clear_stream_active(child_sid)
        except Exception:
            pass
        files: List[str] = []
        try:
            files = sync_child_outputs_to_parent(child_sid, parent_session_id).get(
                "copied", []
            )
        except Exception as e:
            logger.warning("subagent output sync %s failed: %s", child_sid, e)

        text = (final_text if final_text is not None else "".join(tokens)).strip()
        if status == "done" and child_error and not text:
            status = "error"
        # The pipeline replaces an empty answer with its diagnostic warning
        # (e.g. "Il turno è terminato senza testo..."): that is a failure, not
        # a result the parent LLM should relay as the sub-agent's work.
        no_answer = not text or (
            outcome_warning is not None and text == outcome_warning
        )
        if status == "done" and no_answer:
            status = "error"
            child_error = child_error or (
                "the sub-agent ended without an answer"
                + (f" ({outcome_code}: {outcome_warning})" if outcome_warning else "")
            )
        error = None
        if status == "timeout":
            error = f"sub-agent exceeded its time budget ({int(timeout)}s)"
        elif status == "cancelled":
            error = "stopped by the user"
        elif status == "error":
            error = child_error
        result = SubagentResult(
            status=status,
            agent=slug,
            child_conversation_id=child_sid,
            text=truncate_result(text),
            error=error,
            files=files,
        )
        _emit_parent(
            parent_session_id,
            {
                "type": "subagent_end",
                **base_evt,
                "status": status,
                "steps": steps,
                "error": error,
                "result_preview": result.text[:600],
                "files": files,
            },
        )
        await asyncio.shield(_release_child_resources(child_sid))
        logger.info(
            "subagent done parent=%s child=%s profile=%s status=%s steps=%d chars=%d",
            parent_session_id[:12],
            child_sid,
            slug,
            status,
            steps,
            len(text),
        )
    return result


async def _child_runtime(slug: str, user_id: str, timeout: float) -> Dict[str, Any]:
    """Runtime settings for the child turn: user/profile layers + sub-agent timeout.

    Mirrors what the chat API resolves for a normal turn (user presets, profile
    step cap), then pins ``turn_timeout_sec`` to the sub-agent budget so the
    child pipeline stops on its own before the runner has to cancel it.
    """
    runtime: Dict[str, Any] = {}
    try:
        from src.runtime.runtime_settings import (
            profile_step_cap,
            resolve_turn_runtime_settings,
        )

        runtime = await resolve_turn_runtime_settings(
            None, user_id, profile_max_agent_steps=profile_step_cap(slug)
        )
    except Exception as e:
        logger.debug("subagent runtime settings fallback (%s): %s", slug, e)
    runtime = dict(runtime or {})
    runtime["turn_timeout_sec"] = max(10, int(timeout) - 5)
    return runtime
