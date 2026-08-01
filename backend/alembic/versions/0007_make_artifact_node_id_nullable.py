"""0007 Make artifact node_id nullable for seed artifacts

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-22

"""

from typing import Sequence, Union
import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make node_id nullable in artifacts table for seed artifacts (e.g. user_prompt)
    op.alter_column(
        "artifacts",
        "node_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "artifacts",
        "node_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
