"""Add scheduler indexes and artifact uniqueness constraint.

- Index on node_dependencies.depends_on_node_id (reverse DAG lookup)
- Composite index on artifacts(node_id, filename)
- Unique constraint on artifacts(node_id, filename, version)

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Reverse-lookup index for "find all dependents of node X"
    #    The composite PK (node_id, depends_on_node_id) only supports
    #    forward lookups (WHERE node_id = ?). The scheduler also needs
    #    WHERE depends_on_node_id = ? when a node completes.
    op.create_index(
        "ix_node_dependencies_depends_on",
        "node_dependencies",
        ["depends_on_node_id"],
    )

    # 2. Composite index for "get artifacts by node and filename"
    op.create_index(
        "ix_artifacts_node_filename",
        "artifacts",
        ["node_id", "filename"],
    )

    # 3. Unique constraint: no two artifacts can share (node_id, filename, version).
    #    The rework loop produces multiple versions of the same filename,
    #    but each version number must be distinct — DB-level guarantee.
    op.create_unique_constraint(
        "uq_artifacts_node_filename_version",
        "artifacts",
        ["node_id", "filename", "version"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_artifacts_node_filename_version", "artifacts", type_="unique")
    op.drop_index("ix_artifacts_node_filename", table_name="artifacts")
    op.drop_index("ix_node_dependencies_depends_on", table_name="node_dependencies")
