"""Chat folders for the chat-ui sidebar: CRUD + moving a conversation.

Folders are per user and purely organisational: they group conversations in
the sidebar and never change the agent's context (that is what projects do).
A conversation belongs to at most one folder (``conversations.folder_id``);
``NULL`` means "no folder" and is the default for new chats, channel chats
(Telegram/WhatsApp) and every chat created before this feature.

Mounted by ``src/api/main.py`` next to the ``/chat-ui`` router in
``src/api/chat_ui.py`` and called by ``chat-ui/lib/api/aion.ts``. Ownership
follows the rest of ``/chat-ui``: tenant from ``AION_DEFAULT_TENANT_ID`` and
user from ``chat_user_id_scope``; another user's folder or chat answers 404.
See ``docs/clients/chat-folders.md``.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.api.auth.scopes import Scope
from src.api.ownership import chat_user_id_scope
from src.data.engine import get_async_session_maker
from src.data.models import ChatFolder, Conversation

logger = logging.getLogger("aion.api.chat_folders")

router = APIRouter(prefix="/chat-ui", tags=["chat-ui"])

# Same limit as the DB column; longer names would not fit the sidebar anyway.
FOLDER_NAME_MAX = 128
# Guard rail against runaway clients: a sidebar with more folders is unusable.
MAX_FOLDERS_PER_USER = 200


def _require_unified() -> None:
    """Folders live in the unified DB only (same guard as ``chat_ui.py``)."""
    if os.getenv("AION_UNIFIED_DB", "1").lower() not in ("1", "true", "yes"):
        raise HTTPException(503, detail="Unified DB disabled")


def _tenant() -> str:
    return (os.getenv("AION_DEFAULT_TENANT_ID") or "default").strip() or "default"


def _clean_name(raw: Optional[str]) -> str:
    """Trim and validate a folder name; 422 when empty or too long."""
    name = " ".join((raw or "").split())
    if not name:
        raise HTTPException(422, detail="folder_name_required")
    if len(name) > FOLDER_NAME_MAX:
        raise HTTPException(422, detail="folder_name_too_long")
    return name


def _serialize(folder: ChatFolder, count: int = 0) -> Dict[str, Any]:
    return {
        "id": folder.id,
        "name": folder.name,
        "position": folder.position,
        "conversation_count": count,
        "created_at": folder.created_at.isoformat() if folder.created_at else None,
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
    }


async def _get_owned_folder(session, folder_id: str, user_id: str) -> ChatFolder:
    """Load a folder of this user, else 404 (never reveal other users' folders)."""
    folder = await session.get(ChatFolder, folder_id)
    if folder is None or folder.tenant_id != _tenant() or folder.user_id != user_id:
        raise HTTPException(404, detail="folder_not_found")
    return folder


async def _name_taken(
    session, user_id: str, name: str, exclude_id: Optional[str] = None
) -> bool:
    """Case-insensitive duplicate check (the DB unique index is case-sensitive)."""
    q = select(ChatFolder.id).where(
        ChatFolder.tenant_id == _tenant(),
        ChatFolder.user_id == user_id,
        func.lower(ChatFolder.name) == name.lower(),
    )
    if exclude_id:
        q = q.where(ChatFolder.id != exclude_id)
    return (await session.execute(q.limit(1))).first() is not None


class FolderCreateBody(BaseModel):
    name: str


class FolderUpdateBody(BaseModel):
    """Partial update: rename and/or move the folder in the sidebar order."""

    name: Optional[str] = None
    position: Optional[int] = Field(default=None, ge=0)


class ConversationFolderBody(BaseModel):
    """Target folder of a conversation; ``null`` takes it out of any folder."""

    folder_id: Optional[str] = None


@router.get("/folders")
async def list_folders_chat_ui(
    user_id: str = Depends(chat_user_id_scope(Scope.CONVERSATIONS_READ)),
):
    """Folders of the caller in sidebar order, each with its chat count.

    The count covers the same conversations the sidebar lists (not archived,
    no side threads, no branches), so the badge matches what opening the
    folder shows.
    """
    _require_unified()
    tenant = _tenant()
    async with get_async_session_maker()() as session:
        folders = (
            (
                await session.execute(
                    select(ChatFolder)
                    .where(
                        ChatFolder.tenant_id == tenant, ChatFolder.user_id == user_id
                    )
                    .order_by(ChatFolder.position, ChatFolder.created_at)
                )
            )
            .scalars()
            .all()
        )
        counts: Dict[str, int] = {}
        if folders:
            rows = await session.execute(
                select(Conversation.folder_id, func.count(Conversation.id))
                .where(
                    Conversation.tenant_id == tenant,
                    Conversation.user_id == user_id,
                    Conversation.folder_id.in_([f.id for f in folders]),
                    Conversation.archived_at.is_(None),
                    Conversation.parent_conversation_id.is_(None),
                    Conversation.forked_from_id.is_(None),
                )
                .group_by(Conversation.folder_id)
            )
            counts = {fid: int(n) for fid, n in rows.all()}
    return {"data": [_serialize(f, counts.get(f.id, 0)) for f in folders]}


@router.post("/folders", status_code=201)
async def create_folder_chat_ui(
    body: FolderCreateBody,
    user_id: str = Depends(chat_user_id_scope(Scope.CONVERSATIONS_WRITE)),
):
    """Create a folder at the end of the caller's list. 409 if the name exists."""
    _require_unified()
    name = _clean_name(body.name)
    tenant = _tenant()
    async with get_async_session_maker()() as session:
        if await _name_taken(session, user_id, name):
            raise HTTPException(409, detail="folder_name_taken")
        # Count + max position in one query: limit check and "append last".
        n, max_pos = (
            await session.execute(
                select(func.count(ChatFolder.id), func.max(ChatFolder.position)).where(
                    ChatFolder.tenant_id == tenant, ChatFolder.user_id == user_id
                )
            )
        ).one()
        if int(n or 0) >= MAX_FOLDERS_PER_USER:
            raise HTTPException(409, detail="folder_limit_reached")
        now = datetime.now(timezone.utc)
        folder = ChatFolder(
            id=str(uuid.uuid4()),
            tenant_id=tenant,
            user_id=user_id,
            name=name,
            position=(int(max_pos) + 1) if max_pos is not None else 0,
            created_at=now,
            updated_at=now,
        )
        session.add(folder)
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent create with the same name won the unique index.
            await session.rollback()
            raise HTTPException(409, detail="folder_name_taken")
        return _serialize(folder, 0)


@router.patch("/folders/{folder_id}")
async def update_folder_chat_ui(
    folder_id: str,
    body: FolderUpdateBody,
    user_id: str = Depends(chat_user_id_scope(Scope.CONVERSATIONS_WRITE)),
):
    """Rename a folder and/or change its position in the sidebar."""
    _require_unified()
    async with get_async_session_maker()() as session:
        folder = await _get_owned_folder(session, folder_id, user_id)
        if body.name is not None:
            name = _clean_name(body.name)
            if await _name_taken(session, user_id, name, exclude_id=folder.id):
                raise HTTPException(409, detail="folder_name_taken")
            folder.name = name
        if body.position is not None:
            folder.position = body.position
        folder.updated_at = datetime.now(timezone.utc)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, detail="folder_name_taken")
        return _serialize(folder)


@router.delete("/folders/{folder_id}")
async def delete_folder_chat_ui(
    folder_id: str,
    user_id: str = Depends(chat_user_id_scope(Scope.CONVERSATIONS_WRITE)),
):
    """Delete a folder; its chats are kept and go back to "no folder".

    The explicit UPDATE does what ``ON DELETE SET NULL`` would do, so the
    result does not depend on SQLite's ``PRAGMA foreign_keys`` setting or on
    databases patched by ``bootstrap.py`` without the FK constraint.
    """
    _require_unified()
    async with get_async_session_maker()() as session:
        folder = await _get_owned_folder(session, folder_id, user_id)
        released = await session.execute(
            update(Conversation)
            .where(
                Conversation.folder_id == folder.id,
                Conversation.user_id == user_id,
            )
            .values(folder_id=None)
        )
        await session.delete(folder)
        await session.commit()
    return {"deleted": True, "released_conversations": int(released.rowcount or 0)}


@router.put("/conversations/{conv_id}/folder")
async def move_conversation_to_folder_chat_ui(
    conv_id: str,
    body: ConversationFolderBody,
    user_id: str = Depends(chat_user_id_scope(Scope.CONVERSATIONS_WRITE)),
):
    """Put a conversation in a folder (``folder_id``) or out of any (``null``).

    Only persisted conversations can be moved: the sidebar lists nothing else.
    ``updated_at`` is left untouched on purpose, so filing a chat does not
    move it to the top of "Recent".
    """
    _require_unified()
    tenant = _tenant()
    async with get_async_session_maker()() as session:
        conv = await session.get(Conversation, conv_id)
        if conv is None or conv.tenant_id != tenant or conv.user_id != user_id:
            raise HTTPException(404, detail="Conversation not found")
        target: Optional[str] = None
        if body.folder_id:
            # The folder must belong to the same user as the conversation.
            target = (await _get_owned_folder(session, body.folder_id, user_id)).id
        conv.folder_id = target
        await session.commit()
    return {"id": conv_id, "folder_id": target}


def folder_filter_values(raw: Optional[str]) -> Optional[List[Any]]:
    """Parse the ``folder_id`` filter of ``GET /chat-ui/conversations``.

    Returns ``None`` for "no filter", ``[None]`` for ``none`` (chats without
    a folder) and ``[<id>]`` for one folder.
    """
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    if value.lower() == "none":
        return [None]
    return [value]
