"""Add 'blocked' and 'cancelled' to node_status enum.

Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction, but the
new value cannot be USED (INSERT/UPDATE) until that transaction commits.
This migration therefore contains ONLY the ALTER TYPE statements — the new
values are first referenced in migration 0004.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE node_status ADD VALUE IF NOT EXISTS 'blocked'")
    op.execute("ALTER TYPE node_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Postgres does not support removing values from an enum type.
    # The 'blocked' and 'cancelled' values will remain in the enum
    # after downgrade; they are harmless when unused.
    pass
