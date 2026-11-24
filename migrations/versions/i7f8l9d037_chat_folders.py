"""Chat folders: ``chat_folders`` table + ``conversations.folder_id``.

Revision ID: i7f8l9d037
Revises: f3a4b5c034, h6m7r8g036

Per-user folders used by the chat-ui sidebar to group conversations. They are
purely organisational (no effect on agent context, unlike ``projects``).
``conversations.folder_id`` is nullable: NULL means "no folder", which is the
state of every pre-existing conversation, so no data backfill is needed.

This revision also joins the two current heads (``f3a4b5c034`` smb_servers and
``h6m7r8g036`` charts/service-accounts merge): with two heads
``alembic upgrade head`` aborts with "Multiple heads are present".

Every step is idempotent because ``Base.metadata.create_all`` may already
have created the table/column at bootstrap (see ``src/data/bootstrap.py``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i7f8l9d037"
down_revision: Union[str, Sequence[str], None] = ("f3a4b5c034", "h6m7r8g036")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    names = _table_names(bind)

    # 1. Folder table (one row per user folder).
    if "chat_folders" not in names:
        op.create_table(
            "chat_folders",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
            sa.Column("user_id", sa.String(length=256), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "user_id", "name", name="uq_chat_folders_tenant_user_name"
            ),
        )
    if "ix_chat_folders_tenant_user" not in _indexes(bind, "chat_folders"):
        op.create_index(
            "ix_chat_folders_tenant_user",
            "chat_folders",
            ["tenant_id", "user_id"],
            unique=False,
        )

    # 2. Nullable FK on conversations (NULL = no folder, so no backfill).
    if "conversations" not in names:
        return
    if "folder_id" not in _columns(bind, "conversations"):
        folder_col = sa.Column("folder_id", sa.String(length=64), nullable=True)
        if bind.dialect.name == "sqlite":
            # SQLite cannot add a FK constraint in place: batch mode rebuilds the table.
            with op.batch_alter_table("conversations") as batch_op:
                batch_op.add_column(folder_col)
                batch_op.create_foreign_key(
                    "fk_conversations_folder_id",
                    "chat_folders",
                    ["folder_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
        else:
            op.add_column("conversations", folder_col)
            op.create_foreign_key(
                "fk_conversations_folder_id",
                "conversations",
                "chat_folders",
                ["folder_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "ix_conversations_folder_id" not in _indexes(bind, "conversations"):
        op.create_index(
            "ix_conversations_folder_id",
            "conversations",
            ["folder_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    names = _table_names(bind)

    if "conversations" in names and "folder_id" in _columns(bind, "conversations"):
        if "ix_conversations_folder_id" in _indexes(bind, "conversations"):
            op.drop_index("ix_conversations_folder_id", table_name="conversations")
        # Batch mode drops the column together with its FK on SQLite too.
        with op.batch_alter_table("conversations") as batch_op:
            batch_op.drop_column("folder_id")

    if "chat_folders" in names:
        if "ix_chat_folders_tenant_user" in _indexes(bind, "chat_folders"):
            op.drop_index("ix_chat_folders_tenant_user", table_name="chat_folders")
        op.drop_table("chat_folders")
