"""Enforce NOT NULL on run_id and drop output_artifact_ids.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Explicit cleanup order before enforcing NOT NULL
    op.execute("DELETE FROM artifacts")
    op.execute("DELETE FROM nodes")
    op.execute("DELETE FROM runs")

    # Set run_id NOT NULL on nodes and artifacts
    op.alter_column("nodes", "run_id", nullable=False)
    op.alter_column("artifacts", "run_id", nullable=False)

    # Drop obsolete output_artifact_ids column from nodes
    op.drop_column("nodes", "output_artifact_ids")


def downgrade() -> None:
    # Re-add output_artifact_ids column
    op.add_column(
        "nodes",
        sa.Column(
            "output_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
    )

    # Revert NOT NULL constraint on run_id
    op.alter_column("artifacts", "run_id", nullable=True)
    op.alter_column("nodes", "run_id", nullable=True)
