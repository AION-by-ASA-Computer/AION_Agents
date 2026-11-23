"""User groups and access grants (access control, Phase 1).

Revision ID: j8g9m0e038
Revises: i7f8l9d037

Creates ``user_groups``, ``user_group_members`` and ``access_grants`` (see
``src/data/models.py`` and ``PLAN_access_control.md``) and copies every row of
the legacy ``user_profile_access`` into ``access_grants`` as a direct user rule
``profile:<slug>:use``. Users without legacy rows get nothing: "no rule = no
limit", so behaviour is unchanged and there is no default/system group.

The legacy table is kept (read-only, still read by ``src/access/principal.py``)
so a database where this data step never ran (``alembic stamp head`` on a
legacy DB, see ``src/data/migrations.py``) stays correct; it is dropped in a
later release.

Every step is idempotent because ``Base.metadata.create_all`` may already
have created the tables at bootstrap (see ``src/data/bootstrap.py``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j8g9m0e038"
down_revision: Union[str, Sequence[str], None] = "i7f8l9d037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=True,
    )


def upgrade() -> None:
    bind = op.get_bind()
    names = _table_names(bind)

    # 1. Groups (admin-managed, one row per group).
    if "user_groups" not in names:
        op.create_table(
            "user_groups",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=256), nullable=True),
            _created_at(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_user_groups_tenant_slug"),
        )

    # 2. Memberships (users.id <-> user_groups.id).
    if "user_group_members" not in names:
        op.create_table(
            "user_group_members",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("group_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
            sa.Column("added_by", sa.String(length=256), nullable=True),
            sa.Column(
                "added_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["group_id"], ["user_groups.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("group_id", "user_id", name="uq_user_group_members"),
        )
    member_idx = _indexes(bind, "user_group_members")
    for col in ("group_id", "user_id"):
        name = f"ix_user_group_members_{col}"
        if name not in member_idx:
            op.create_index(name, "user_group_members", [col], unique=False)

    # 3. Access rules for users and groups.
    if "access_grants" not in names:
        op.create_table(
            "access_grants",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.String(length=64),
                nullable=False,
                server_default="default",
            ),
            sa.Column("subject_type", sa.String(length=16), nullable=False),
            sa.Column("subject_id", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=32), nullable=False),
            sa.Column("resource_id", sa.String(length=256), nullable=False),
            sa.Column(
                "action", sa.String(length=16), nullable=False, server_default="use"
            ),
            sa.Column("created_by", sa.String(length=256), nullable=True),
            _created_at(),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "subject_type",
                "subject_id",
                "resource_type",
                "resource_id",
                "action",
                name="uq_access_grants",
            ),
        )
    if "ix_access_grants_subject" not in _indexes(bind, "access_grants"):
        op.create_index(
            "ix_access_grants_subject",
            "access_grants",
            ["tenant_id", "subject_type", "subject_id"],
            unique=False,
        )

    # 4. Data: legacy per-user profile allowlists become direct user rules.
    #    NOT EXISTS keeps the copy idempotent; ORDER BY id preserves the order
    #    used for the default-profile fallback.
    if "user_profile_access" in names:
        bind.execute(
            sa.text(
                """
                INSERT INTO access_grants
                    (tenant_id, subject_type, subject_id, resource_type,
                     resource_id, action, created_by)
                SELECT upa.tenant_id, 'user', upa.user_id, 'profile',
                       upa.profile_slug, 'use', 'migration:j8g9m0e038'
                FROM user_profile_access upa
                WHERE NOT EXISTS (
                    SELECT 1 FROM access_grants g
                    WHERE g.tenant_id = upa.tenant_id
                      AND g.subject_type = 'user'
                      AND g.subject_id = upa.user_id
                      AND g.resource_type = 'profile'
                      AND g.resource_id = upa.profile_slug
                      AND g.action = 'use'
                )
                ORDER BY upa.id
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    names = _table_names(bind)
    # The legacy table still holds the profile allowlists: nothing to copy back.
    for table in ("access_grants", "user_group_members", "user_groups"):
        if table in names:
            op.drop_table(table)
