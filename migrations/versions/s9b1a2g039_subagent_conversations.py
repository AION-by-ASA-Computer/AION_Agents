"""Sub-agent child conversations: conversations.spawned_by_conversation_id.

Revision ID: s9b1a2g039
Revises: b7r8n9d038

When the main agent delegates a task with the ``delegate_to_agent`` tool, the
sub-agent runs in its own conversation (``sub_<slug>_<hex>``). This column links
that child conversation to the conversation that spawned it, so that:

- the sidebar can hide child runs with an indexed SQL filter (instead of a
  Python-side metadata filter that would crowd out real chats);
- the chat-ui can open the child as a read-only transcript with a link back;
- archiving the parent archives its sub-agent runs too.

``parent_conversation_id`` is intentionally NOT reused: it marks side threads,
whose turns get the parent transcript injected (sub-agents must stay isolated).
See docs/agents/subagents.md.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s9b1a2g039"
down_revision: Union[str, None] = "b7r8n9d038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "conversations"
_COL = "spawned_by_conversation_id"
_INDEX = "ix_conversations_spawned_by_conversation_id"


def upgrade() -> None:
    # Idempotent: bootstrap.py may already have added the column on SQLite.
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    if _COL not in {c["name"] for c in inspector.get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column(_COL, sa.String(64), nullable=True))
    if _INDEX not in {i["name"] for i in inspector.get_indexes(_TABLE)}:
        op.create_index(_INDEX, _TABLE, [_COL])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    if _INDEX in {i["name"] for i in inspector.get_indexes(_TABLE)}:
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COL in {c["name"] for c in inspector.get_columns(_TABLE)}:
        # batch mode: SQLite cannot DROP COLUMN in place on older versions.
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COL)
