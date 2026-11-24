"""Chat deletion purges the conversation and every artefact tied to it.

Covers ``DELETE /chat-ui/conversations/{id}`` (``delete_conversation_chat_ui``)
backed by ``src/runtime/conversation_purge.py``: DB rows (messages, steps,
attachments, feedbacks, FTS), branches and side threads, the session folder
``data/sessions/<id>/``, object storage files and in-memory runtime state.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from src.storage.local_backend import LocalBackend


async def _reset_db(monkeypatch, tmp_path: Path) -> LocalBackend:
    """Fresh SQLite DB + data dir + local storage backend under ``tmp_path``."""
    import src.data.engine as engine

    if engine._engine is not None:
        await engine._engine.dispose()
    engine._engine = None
    engine._session_factory = None
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    monkeypatch.setenv("AION_DEFAULT_TENANT_ID", "default")
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'aion.db'}")
    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AION_SANDBOX_FLAT_SESSION_ROOT", raising=False)
    from src.settings import get_settings

    get_settings.cache_clear()
    backend = LocalBackend(tmp_path / "storage")
    monkeypatch.setattr("src.storage.get_storage_backend", lambda: backend)
    return backend


async def _seed_chat(conv_id: str, user_id: str, **conv_fields) -> None:
    """Create a 2-message conversation, optionally setting extra columns."""
    from src.data.engine import get_async_session_maker
    from src.data.history_bridge import UnifiedHistoryBridge
    from src.data.models import Conversation

    bridge = UnifiedHistoryBridge()
    await bridge.add_message(
        conv_id,
        "user",
        f"domanda segreta {conv_id}",
        user_id=user_id,
        message_id=f"{conv_id}-u",
    )
    await bridge.add_message(
        conv_id,
        "assistant",
        f"risposta {conv_id}",
        user_id=user_id,
        message_id=f"{conv_id}-a",
    )
    if conv_fields:
        async with get_async_session_maker()() as session:
            conv = await session.get(Conversation, conv_id)
            for k, v in conv_fields.items():
                setattr(conv, k, v)
            await session.commit()


async def _count(model, **where) -> int:
    from src.data.engine import get_async_session_maker

    async with get_async_session_maker()() as session:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int((await session.execute(q)).scalar_one())


def test_delete_purges_db_folder_storage_and_family(monkeypatch, tmp_path):
    """Deleting a root chat removes it, its branch and side thread, files and
    storage objects, while another chat of the same user is untouched."""
    from src.api.chat_ui import delete_conversation_chat_ui
    from src.data.engine import get_async_session_maker
    from src.data.models import (
        Attachment,
        Conversation,
        Feedback,
        Message,
        Step,
    )
    from src.session_workspace import ensure_session_dirs

    async def scenario():
        backend = await _reset_db(monkeypatch, tmp_path)
        await _seed_chat("conv-root", "u1")
        await _seed_chat(
            "conv-branch",
            "u1",
            root_conversation_id="conv-root",
            forked_from_id="conv-root",
        )
        await _seed_chat("conv-thread", "u1", parent_conversation_id="conv-root")
        await _seed_chat("conv-keep", "u1")

        # Steps, attachments, feedbacks attached to the root chat.
        async with get_async_session_maker()() as session:
            session.add(
                Step(
                    id="s1",
                    conversation_id="conv-root",
                    message_id="conv-root-a",
                    name="tool",
                    type="tool",
                )
            )
            session.add(
                Attachment(
                    id="att1",
                    conversation_id="conv-root",
                    storage_key="default/conversations/conv-root/uploads/a.txt",
                    original_name="a.txt",
                    mime="text/plain",
                    size_bytes=1,
                    kind="upload",
                )
            )
            session.add(
                Feedback(
                    id="f1",
                    conversation_id="conv-root",
                    message_id="conv-root-a",
                    value=1,
                )
            )
            await session.commit()

        # Session folders and storage objects for every chat.
        for cid in ("conv-root", "conv-branch", "conv-thread", "conv-keep"):
            root = ensure_session_dirs(cid)
            (root / "uploads" / "file.txt").write_text("x")
            (root / "derived" / "tool_results").mkdir(parents=True, exist_ok=True)
            (root / "derived" / "tool_results" / "r.txt").write_text("y")
            backend.put_object(
                f"default/conversations/{cid}/uploads/a.txt", b"z", "text/plain"
            )

        res = await delete_conversation_chat_ui("conv-root", user_id="u1")
        assert res["success"] is True
        assert set(res["deleted_ids"]) == {"conv-root", "conv-branch", "conv-thread"}

        for cid in ("conv-root", "conv-branch", "conv-thread"):
            assert await _count(Conversation, id=cid) == 0
            assert await _count(Message, conversation_id=cid) == 0
            assert not (tmp_path / "data" / "sessions" / cid).exists()
            assert backend.list_prefix(f"default/conversations/{cid}/") == []
        assert await _count(Step, conversation_id="conv-root") == 0
        assert await _count(Attachment, conversation_id="conv-root") == 0
        assert await _count(Feedback, conversation_id="conv-root") == 0

        # Full-text index no longer returns the deleted text.
        async with get_async_session_maker()() as session:
            hits = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'segreta'"
                    )
                )
            ).scalar_one()
        assert hits == 1  # only conv-keep

        # Unrelated chat is intact.
        assert await _count(Conversation, id="conv-keep") == 1
        assert await _count(Message, conversation_id="conv-keep") == 2
        assert (
            tmp_path / "data" / "sessions" / "conv-keep" / "uploads" / "file.txt"
        ).is_file()
        assert backend.list_prefix("default/conversations/conv-keep/") != []

    asyncio.run(scenario())


def test_delete_branch_keeps_root(monkeypatch, tmp_path):
    """Deleting a single branch removes only that branch, not the root chat."""
    from src.api.chat_ui import delete_conversation_chat_ui
    from src.data.models import Conversation

    async def scenario():
        await _reset_db(monkeypatch, tmp_path)
        await _seed_chat("conv-root", "u1")
        await _seed_chat(
            "conv-branch",
            "u1",
            root_conversation_id="conv-root",
            forked_from_id="conv-root",
        )
        res = await delete_conversation_chat_ui("conv-branch", user_id="u1")
        assert res["deleted_ids"] == ["conv-branch"]
        assert await _count(Conversation, id="conv-root") == 1
        assert await _count(Conversation, id="conv-branch") == 0

    asyncio.run(scenario())


def test_delete_other_user_is_404_and_keeps_data(monkeypatch, tmp_path):
    """A user cannot delete someone else's chat; nothing is removed."""
    from src.api.chat_ui import delete_conversation_chat_ui
    from src.data.models import Conversation
    from src.session_workspace import ensure_session_dirs

    async def scenario():
        await _reset_db(monkeypatch, tmp_path)
        await _seed_chat("conv-a", "alice")
        ensure_session_dirs("conv-a")
        with pytest.raises(HTTPException) as exc:
            await delete_conversation_chat_ui("conv-a", user_id="bob")
        assert exc.value.status_code == 404
        assert await _count(Conversation, id="conv-a") == 1
        assert (tmp_path / "data" / "sessions" / "conv-a").is_dir()

    asyncio.run(scenario())


def test_offload_cleanup_does_not_recreate_deleted_folder(monkeypatch, tmp_path):
    """The archived-offload cleanup must not resurrect a removed session folder."""
    from src.runtime.tool_offload import cleanup_session_offloads

    monkeypatch.setenv("AION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AION_SANDBOX_FLAT_SESSION_ROOT", raising=False)
    assert cleanup_session_offloads("conv-gone") == 0
    assert not (tmp_path / "data" / "sessions" / "conv-gone").exists()


def test_delete_evicts_runtime_state(monkeypatch, tmp_path):
    """MCP pool/context and cached agents of the deleted session are released."""
    import src.main as main_mod
    from src.api.chat_ui import delete_conversation_chat_ui
    from src.mcp_manager import mcp_manager

    async def scenario():
        await _reset_db(monkeypatch, tmp_path)
        await _seed_chat("conv-rt", "u1")
        main_mod._AGENT_CACHE["conv-rt\0prof\0sig"] = (object(), "x")
        main_mod._AGENT_CACHE["other\0prof\0sig"] = (object(), "x")
        mcp_manager._session_ctx["conv-rt"] = ("prof", "u1", "default")
        try:
            await delete_conversation_chat_ui("conv-rt", user_id="u1")
            assert "conv-rt\0prof\0sig" not in main_mod._AGENT_CACHE
            assert "other\0prof\0sig" in main_mod._AGENT_CACHE
            assert "conv-rt" not in mcp_manager._session_ctx
        finally:
            main_mod._AGENT_CACHE.pop("other\0prof\0sig", None)
            main_mod._AGENT_CACHE.pop("conv-rt\0prof\0sig", None)
            mcp_manager._session_ctx.pop("conv-rt", None)

    asyncio.run(scenario())
