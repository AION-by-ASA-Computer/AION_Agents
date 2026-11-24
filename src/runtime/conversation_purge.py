"""Hard deletion of chat conversations and every artefact tied to them.

Deleting a chat used to be a soft delete (``conversations.archived_at``): the
messages, tool steps, attachments, the sandbox folder ``data/sessions/<id>/``
and the object-storage files stayed around forever (still readable through
``GET /chat-ui/conversations/{id}/messages`` and matched by the history FTS).
This module removes them for real.

What :func:`purge_conversations` removes, for each conversation id:

* runtime state: pooled MCP stdio workers and sandbox container, MCP session
  context, cached agents and MCP wrapper functions, live chart store;
* DB rows: feedbacks, session charts, steps, attachments, messages (the FTS
  index follows via the ``messages`` triggers), execution plans and the
  conversation row itself;
* object storage under ``<tenant>/conversations/<id>/`` plus any attachment
  whose ``storage_key`` lives elsewhere;
* the session folder ``data/sessions/<id>/`` (uploads, workspace, derived,
  offloaded tool results, ledger, venv).

What is intentionally kept: long-term memory notes (user-level memory, managed
from the memory panel; ``ltm_notes.source_session_id`` is only provenance),
audit logs, cron run history and channel message logs (they only reference the
id as metadata; the WhatsApp/Telegram channels start a new conversation when
their active one no longer exists).

Used by ``DELETE /chat-ui/conversations/{id}`` (``src/api/chat_ui.py``) and
``DELETE /v1/conversations/{id}`` (``src/api/v1/conversations.py``).
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, Iterable, List, Set

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.engine import get_async_session_maker
from src.data.models import (
    Attachment,
    Conversation,
    ExecutionPlanRecord,
    Feedback,
    Message,
    SessionChart,
    Step,
)

logger = logging.getLogger("aion.conversation_purge")


async def collect_conversation_family(
    session: AsyncSession, conv: Conversation
) -> List[str]:
    """Return the ids to delete together with ``conv`` (``conv`` first).

    * If ``conv`` is a root chat (not itself a branch), its branches
      (``root_conversation_id == conv.id``) go too: they are hidden from the
      sidebar and only reachable through the root, so they would be orphaned.
      Deleting a single branch leaves the root and sibling branches intact.
    * Side threads (``parent_conversation_id``) of every collected chat go too.

    Only rows of the same tenant and user are collected.
    """
    ids: List[str] = [conv.id]
    scope = (
        Conversation.tenant_id == conv.tenant_id,
        Conversation.user_id == conv.user_id,
    )
    if not conv.forked_from_id:
        branch_ids = (
            (
                await session.execute(
                    select(Conversation.id).where(
                        Conversation.root_conversation_id == conv.id, *scope
                    )
                )
            )
            .scalars()
            .all()
        )
        ids.extend(b for b in branch_ids if b not in ids)
    thread_ids = (
        (
            await session.execute(
                select(Conversation.id).where(
                    Conversation.parent_conversation_id.in_(list(ids)), *scope
                )
            )
        )
        .scalars()
        .all()
    )
    ids.extend(t for t in thread_ids if t not in ids)
    return ids


async def _release_runtime(conv_id: str) -> None:
    """Stop MCP workers/container and drop in-memory state of one session.

    Done before touching the disk so no worker keeps writing into the session
    folder while it is removed. Errors are logged: a failed release must not
    block the deletion of the user's data.
    """
    try:
        from src.mcp_manager import mcp_manager

        await mcp_manager.release_session(conv_id, forget_context=True)
    except Exception as exc:
        logger.warning("purge: MCP release failed for %s: %s", conv_id[:12], exc)
    try:
        from src.main import evict_session_agents

        await evict_session_agents(conv_id)
    except Exception as exc:
        logger.warning("purge: agent cache eviction failed for %s: %s", conv_id, exc)
    try:
        from src.chart_queue import chart_queue

        chart_queue.clear(conv_id)
    except Exception as exc:
        logger.debug("purge: live chart clear failed for %s: %s", conv_id, exc)


def _delete_storage_objects(
    conv_ids: Iterable[str], tenant_id: str, extra_keys: Iterable[str]
) -> int:
    """Delete object-storage files of the conversations. Returns objects removed.

    Uploads and promoted outputs live under ``<tenant>/conversations/<id>/``
    (``src/api/v1/files.py``, ``src/storage/session_cache.py``,
    ``src/channels/whatsapp/media.py``); ``extra_keys`` are attachment keys
    stored elsewhere.
    """
    try:
        from src.storage import get_storage_backend

        backend = get_storage_backend()
    except Exception as exc:
        logger.warning("purge: storage backend unavailable: %s", exc)
        return 0
    removed = 0
    prefixes = [f"{tenant_id}/conversations/{cid}/" for cid in conv_ids]
    for prefix in prefixes:
        try:
            removed += backend.delete_prefix(prefix)
        except Exception as exc:
            logger.warning("purge: storage delete_prefix %s failed: %s", prefix, exc)
    for key in extra_keys:
        if not key or any(key.startswith(p) for p in prefixes):
            continue
        try:
            if backend.delete_object(key):
                removed += 1
        except Exception as exc:
            logger.warning("purge: storage delete_object %s failed: %s", key, exc)
    return removed


def _delete_session_folder(conv_id: str) -> bool:
    """Remove ``data/sessions/<id>/`` if present. Returns True when removed.

    Path is built with ``session_fs`` (validated id, confined to the data
    root) and never via ``safe_resolve``/``ensure_session_dirs``, which would
    re-create the folder. Skipped under the flat sandbox mount, where the
    session root *is* the data root.
    """
    from src.session_workspace import _flat_session_mount, session_fs

    if _flat_session_mount():
        return False
    try:
        path = session_fs(conv_id)
    except ValueError:
        # Id not shaped like a session id: no folder can exist for it.
        return False
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        logger.warning("purge: session folder %s not fully removed", path)
        return False
    return True


async def purge_conversations(conv_ids: List[str], *, tenant_id: str) -> Dict[str, Any]:
    """Permanently delete the given conversations and all their data.

    Callers must have already checked ownership of every id (see
    :func:`collect_conversation_family`). Order: runtime release, DB rows in a
    single transaction (children before parents, explicit so it does not rely
    on SQLite ``PRAGMA foreign_keys`` or DB-level cascades), then object
    storage and session folders (best effort, after the commit so a DB error
    never leaves rows pointing at deleted files).

    Returns a summary: ``conversation_ids``, ``storage_objects``,
    ``session_folders``.
    """
    ids = [c for c in dict.fromkeys(conv_ids) if c]
    if not ids:
        return {"conversation_ids": [], "storage_objects": 0, "session_folders": 0}

    for cid in ids:
        await _release_runtime(cid)

    attachment_keys: Set[str] = set()
    async with get_async_session_maker()() as session:
        # Attachment keys first: their storage objects are deleted after commit.
        attachment_keys.update(
            k
            for k in (
                await session.execute(
                    select(Attachment.storage_key).where(
                        Attachment.conversation_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
            if k
        )
        msg_ids = select(Message.id).where(Message.conversation_id.in_(ids))
        # Feedback rows may point at messages/steps of these chats.
        await session.execute(
            delete(Feedback).where(
                or_(Feedback.conversation_id.in_(ids), Feedback.message_id.in_(msg_ids))
            )
        )
        await session.execute(
            delete(SessionChart).where(SessionChart.conversation_id.in_(ids))
        )
        await session.execute(delete(Step).where(Step.conversation_id.in_(ids)))
        await session.execute(
            delete(Attachment).where(Attachment.conversation_id.in_(ids))
        )
        # Message delete fires the FTS triggers, so the text leaves the index.
        await session.execute(delete(Message).where(Message.conversation_id.in_(ids)))
        await session.execute(
            delete(ExecutionPlanRecord).where(ExecutionPlanRecord.session_id.in_(ids))
        )
        await session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        await session.commit()

    storage_objects = _delete_storage_objects(ids, tenant_id, attachment_keys)
    session_folders = sum(1 for cid in ids if _delete_session_folder(cid))

    logger.info(
        "conversation purge: conversations=%d storage_objects=%d session_folders=%d",
        len(ids),
        storage_objects,
        session_folders,
    )
    return {
        "conversation_ids": ids,
        "storage_objects": storage_objects,
        "session_folders": session_folders,
    }
