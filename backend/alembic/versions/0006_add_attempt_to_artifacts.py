"""0006 Add attempt to artifacts and index (run_id, kind, attempt)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22

"""

from typing import Sequence, Union
import alembic.op as op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add attempt column to artifacts table
    op.add_column(
        "artifacts",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )

    # 2. Add composite index on (run_id, kind, attempt)
    op.create_index(
        "ix_artifacts_run_kind_attempt",
        "artifacts",
        ["run_id", "kind", "attempt"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Drop index
    op.drop_index("ix_artifacts_run_kind_attempt", table_name="artifacts")

    # 2. Drop attempt column
    op.drop_column("artifacts", "attempt")
