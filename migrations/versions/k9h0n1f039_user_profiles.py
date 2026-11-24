"""Private user profiles (access control, Phase 3).

Revision ID: k9h0n1f039
Revises: j8g9m0e038

Creates ``user_profiles``: agent profiles created by end users from chat-ui,
usable only by their owner (see ``src/data/models.py`` ``UserProfile`` and
``src/access/private_profiles.py``). Idempotent because
``Base.metadata.create_all`` may already have created the table at bootstrap.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k9h0n1f039"
down_revision: Union[str, Sequence[str], None] = "j8g9m0e038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "user_profiles" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("skills_json", sa.Text(), nullable=False),
        sa.Column("mcp_servers_json", sa.Text(), nullable=False),
        sa.Column("native_tool_groups_json", sa.Text(), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="private",
        ),
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
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_user_profiles_slug"),
    )
    op.create_index(
        "ix_user_profiles_owner_user_id", "user_profiles", ["owner_user_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_profiles" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("user_profiles")
