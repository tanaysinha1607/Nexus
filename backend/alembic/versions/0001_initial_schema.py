"""Initial schema: projects, nodes, artifacts, node_dependencies.

Revision ID: 0001
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- Enum types --
    project_status = postgresql.ENUM(
        "pending", "running", "completed", "failed",
        name="project_status",
        create_type=True,
    )
    node_type = postgresql.ENUM(
        "agent", "executor", "validator",
        name="node_type",
        create_type=True,
    )
    node_status = postgresql.ENUM(
        "pending", "ready", "running", "needs_review", "failed", "completed",
        name="node_status",
        create_type=True,
    )

    # -- projects --
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("user_prompt", sa.Text, nullable=False),
        sa.Column("status", project_status, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -- nodes --
    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("node_type", node_type, nullable=False),
        sa.Column("agent_role", sa.String(100), nullable=True),
        sa.Column("status", node_status, nullable=False, server_default="pending"),
        sa.Column("input_artifact_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), server_default="{}"),
        sa.Column("output_artifact_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), server_default="{}"),
        sa.Column("config", postgresql.JSONB, server_default="{}"),
        sa.Column("logs", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -- artifacts --
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("content_type", sa.String(100), nullable=False, server_default="text/plain"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -- node_dependencies (DAG edges) --
    op.create_table(
        "node_dependencies",
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
        sa.UniqueConstraint("node_id", "depends_on_node_id", name="uq_node_dependency"),
    )


def downgrade() -> None:
    op.drop_table("node_dependencies")
    op.drop_table("artifacts")
    op.drop_table("nodes")
    op.drop_table("projects")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS node_status")
    op.execute("DROP TYPE IF EXISTS node_type")
    op.execute("DROP TYPE IF EXISTS project_status")
