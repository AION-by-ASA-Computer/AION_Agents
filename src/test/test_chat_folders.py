"""Chat folders (``src/api/chat_folders.py``) and migration ``i7f8l9d037``.

Covers folder CRUD, moving conversations in/out of folders, per-user
isolation, the ``folder_id`` filter of ``GET /chat-ui/conversations`` and the
idempotent upgrade/downgrade of the migration.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text

from src.api.chat_folders import (
    ConversationFolderBody,
    FolderCreateBody,
    FolderUpdateBody,
    create_folder_chat_ui,
    delete_folder_chat_ui,
    list_folders_chat_ui,
    move_conversation_to_folder_chat_ui,
    update_folder_chat_ui,
)
from src.api.chat_ui import get_conversation_chat_ui, list_conversations_chat_ui
from src.data.history_bridge import UnifiedHistoryBridge

_ROOT = Path(__file__).resolve().parents[2]


async def _reset_unified_db(monkeypatch, tmp_path: Path) -> None:
    """Point the unified DB at a fresh SQLite file for each test."""
    import src.data.engine as engine

    if engine._engine is not None:
        await engine._engine.dispose()
    engine._engine = None
    engine._session_factory = None
    monkeypatch.setenv("AION_UNIFIED_DB", "1")
    monkeypatch.setenv("AION_DEFAULT_TENANT_ID", "default")
    monkeypatch.setenv("AION_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'aion.db'}")
    from src.settings import get_settings

    get_settings.cache_clear()
    # Create the schema as app startup does (tests may not seed any message).
    from src.data.bootstrap import ensure_bootstrap_schema

    await ensure_bootstrap_schema(engine.get_engine())


async def _seed(conv_id: str, user_id: str = "u1") -> None:
    """Create a persisted conversation with one message."""
    await UnifiedHistoryBridge().add_message(
        conv_id, "user", f"hello {conv_id}", user_id=user_id
    )


async def _list(user_id: str = "u1", folder_id=None):
    return (
        await list_conversations_chat_ui(
            limit=40,
            exclude_scheduled=True,
            include_threads=False,
            include_branches=False,
            folder_id=folder_id,
            user_id=user_id,
        )
    )["data"]


def test_crud_and_ordering(monkeypatch, tmp_path):
    """Create appends at the end, rename works, duplicates (any case) are 409."""

    async def run():
        await _reset_unified_db(monkeypatch, tmp_path)
        a = await create_folder_chat_ui(
            FolderCreateBody(name="  Lavoro  "), user_id="u1"
        )
        b = await create_folder_chat_ui(
            FolderCreateBody(name="Personale"), user_id="u1"
        )
        assert a["name"] == "Lavoro" and a["position"] == 0
        assert b["position"] == 1

        with pytest.raises(HTTPException) as dup:
            await create_folder_chat_ui(FolderCreateBody(name="lavoro"), user_id="u1")
        assert dup.value.status_code == 409

        with pytest.raises(HTTPException) as empty:
            await create_folder_chat_ui(FolderCreateBody(name="   "), user_id="u1")
        assert empty.value.status_code == 422

        renamed = await update_folder_chat_ui(
            a["id"], FolderUpdateBody(name="Clienti", position=5), user_id="u1"
        )
        assert renamed["name"] == "Clienti" and renamed["position"] == 5

        names = [f["name"] for f in (await list_folders_chat_ui(user_id="u1"))["data"]]
        assert names == ["Personale", "Clienti"]

    asyncio.run(run())


def test_move_filter_and_delete_releases_chats(monkeypatch, tmp_path):
    """Chats default to no folder; moving and deleting the folder update the lists."""

    async def run():
        await _reset_unified_db(monkeypatch, tmp_path)
        await _seed("c1")
        await _seed("c2")
        folder = await create_folder_chat_ui(FolderCreateBody(name="F"), user_id="u1")

        # Pre-existing / new chats have no folder.
        assert {c["folder_id"] for c in await _list()} == {None}

        moved = await move_conversation_to_folder_chat_ui(
            "c1", ConversationFolderBody(folder_id=folder["id"]), user_id="u1"
        )
        assert moved == {"id": "c1", "folder_id": folder["id"]}
        assert [c["id"] for c in await _list(folder_id=folder["id"])] == ["c1"]
        assert [c["id"] for c in await _list(folder_id="none")] == ["c2"]
        assert len(await _list()) == 2  # no filter = every chat
        details = await get_conversation_chat_ui("c1", user_id="u1")
        assert details["folder_id"] == folder["id"]

        listed = (await list_folders_chat_ui(user_id="u1"))["data"]
        assert listed[0]["conversation_count"] == 1

        # Back out of the folder with null.
        await move_conversation_to_folder_chat_ui(
            "c1", ConversationFolderBody(folder_id=None), user_id="u1"
        )
        assert await _list(folder_id=folder["id"]) == []

        # Deleting a folder keeps its chats and sends them back to "no folder".
        await move_conversation_to_folder_chat_ui(
            "c2", ConversationFolderBody(folder_id=folder["id"]), user_id="u1"
        )
        res = await delete_folder_chat_ui(folder["id"], user_id="u1")
        assert res == {"deleted": True, "released_conversations": 1}
        assert {c["id"] for c in await _list(folder_id="none")} == {"c1", "c2"}
        assert (await list_folders_chat_ui(user_id="u1"))["data"] == []

    asyncio.run(run())


def test_other_users_folders_and_chats_are_404(monkeypatch, tmp_path):
    """A user can neither see, edit nor use another user's folder or chat."""

    async def run():
        await _reset_unified_db(monkeypatch, tmp_path)
        await _seed("mine", user_id="u1")
        await _seed("theirs", user_id="u2")
        foreign = await create_folder_chat_ui(FolderCreateBody(name="X"), user_id="u2")
        # Same name is fine for a different user.
        await create_folder_chat_ui(FolderCreateBody(name="X"), user_id="u1")

        assert len((await list_folders_chat_ui(user_id="u1"))["data"]) == 1
        for call in (
            update_folder_chat_ui(
                foreign["id"], FolderUpdateBody(name="Y"), user_id="u1"
            ),
            delete_folder_chat_ui(foreign["id"], user_id="u1"),
            move_conversation_to_folder_chat_ui(
                "mine", ConversationFolderBody(folder_id=foreign["id"]), user_id="u1"
            ),
            move_conversation_to_folder_chat_ui(
                "theirs", ConversationFolderBody(folder_id=None), user_id="u1"
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 404

    asyncio.run(run())


def _load_migration():
    path = _ROOT / "migrations/versions/i7f8l9d037_chat_folders.py"
    spec = importlib.util.spec_from_file_location("mig_i7f8l9d037", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(eng, fn_name):
    mod = _load_migration()
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(mod, fn_name)()


def test_migration_upgrade_downgrade(tmp_path):
    """Migration merges both heads, is idempotent and keeps existing chats."""
    mod = _load_migration()
    assert set(mod.down_revision) == {"f3a4b5c034", "h6m7r8g036"}

    eng = create_engine(f"sqlite:///{tmp_path}/m.db")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE conversations (id VARCHAR(64) PRIMARY KEY, "
                "tenant_id VARCHAR(64), user_id VARCHAR(256), title VARCHAR(512))"
            )
        )
        conn.execute(
            text("INSERT INTO conversations VALUES ('old', 'default', 'u1', 'T')")
        )

    _run(eng, "upgrade")
    _run(eng, "upgrade")  # idempotent
    insp = inspect(eng)
    assert "chat_folders" in insp.get_table_names()
    assert "folder_id" in {c["name"] for c in insp.get_columns("conversations")}
    assert "ix_conversations_folder_id" in {
        i["name"] for i in insp.get_indexes("conversations")
    }
    with eng.connect() as conn:
        # Pre-existing chat survives the batch rebuild with no folder.
        assert (
            conn.execute(
                text("SELECT folder_id FROM conversations WHERE id='old'")
            ).scalar_one()
            is None
        )

    _run(eng, "downgrade")
    insp = inspect(eng)
    assert "chat_folders" not in insp.get_table_names()
    assert "folder_id" not in {c["name"] for c in insp.get_columns("conversations")}


def test_migration_matches_models():
    """Migration columns match the ORM ``ChatFolder`` model."""
    from src.data.models import ChatFolder

    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE tenants (id VARCHAR(64) PRIMARY KEY)"))
    _run(eng, "upgrade")
    cols = {c["name"] for c in inspect(eng).get_columns("chat_folders")}
    assert cols == {c.name for c in ChatFolder.__table__.columns}
