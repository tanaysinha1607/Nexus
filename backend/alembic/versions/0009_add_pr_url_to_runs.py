"""0009 Add pr_url column to runs table

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-03

"""

from typing import Sequence, Union
import alembic.op as op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("pr_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "pr_url")
