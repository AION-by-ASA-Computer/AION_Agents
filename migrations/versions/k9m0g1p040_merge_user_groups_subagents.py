"""Merge revision: user groups/access grants + subagent conversations.

Revision ID: k9m0g1p040
Revises: j8g9m0e038, s9b1a2g039

No schema change. The user-groups branch (``j8g9m0e038``) and ``main``
(``s9b1a2g039``, subagent conversations) both descend from ``i7f8l9d037``,
which left Alembic with two heads and made ``alembic upgrade head`` fail.
A merge revision (instead of re-parenting one side) keeps every database
upgradable whichever of the two heads it was stamped at.
"""

from typing import Sequence, Union


revision: str = "k9m0g1p040"
down_revision: Union[str, Sequence[str], None] = ("j8g9m0e038", "s9b1a2g039")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision only joins the two migration branches."""


def downgrade() -> None:
    """No-op: downgrading splits back into the two parent heads."""
