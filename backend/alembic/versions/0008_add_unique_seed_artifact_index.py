"""0008 Add unique partial index for seed artifacts

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-22

"""

from typing import Sequence, Union
import alembic.op as op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create partial unique index enforcing uniqueness for seed artifacts (node_id IS NULL)
    op.create_index(
        "uq_artifacts_seed_run_kind_filename",
        "artifacts",
        ["run_id", "kind", "filename"],
        unique=True,
        postgresql_where=sa.text("node_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_artifacts_seed_run_kind_filename",
        table_name="artifacts",
        postgresql_where=sa.text("node_id IS NULL"),
    )
