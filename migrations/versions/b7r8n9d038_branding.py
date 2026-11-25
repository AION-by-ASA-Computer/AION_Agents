"""Tenant branding: primary colours, logo and favicon.

Revision ID: b7r8n9d038
Revises: h6m7r8g036, f3a4b5c034

Adds ``branding_settings`` (one row per tenant with the light/dark primary
colours) and ``branding_assets`` (logo_light / logo_dark / favicon bytes),
edited from admin-ui (Settings -> Brand) via ``/admin/branding`` and read
pre-login through the public ``GET /branding``.

It also joins the two heads present on ``main`` (charts/service accounts and
SMB servers) so the chain has a single head again.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7r8n9d038"
down_revision: Union[str, Sequence[str], None] = ("h6m7r8g036", "f3a4b5c034")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: the tables may already exist when created by create_all at bootstrap.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "branding_settings" not in tables:
        op.create_table(
            "branding_settings",
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("primary_color_light", sa.String(length=7), nullable=True),
            sa.Column("primary_color_dark", sa.String(length=7), nullable=True),
            sa.Column("updated_by_user_id", sa.String(length=64), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("tenant_id"),
        )
    if "branding_assets" not in tables:
        op.create_table(
            "branding_assets",
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("mime", sa.String(length=64), nullable=False),
            sa.Column("data", sa.LargeBinary(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("updated_by_user_id", sa.String(length=64), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("tenant_id", "kind"),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "branding_assets" in tables:
        op.drop_table("branding_assets")
    if "branding_settings" in tables:
        op.drop_table("branding_settings")
