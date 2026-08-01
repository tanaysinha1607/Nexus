"""Add runs table, extend nodes and artifacts, add immutability trigger.

- CREATE run_status enum
- CREATE TABLE runs (with project_id FK, seq_counter, timestamps)
- ALTER TABLE nodes: add run_id, attempt, rework_of, claimed_by,
  lease_expires_at; drop input_artifact_ids
- ALTER TABLE artifacts: add kind, produced_by_role, run_id
- CREATE immutability trigger on artifacts (BEFORE UPDATE raises exception)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # run_status enum
    # -----------------------------------------------------------------------
    run_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", "cancelled",
        name="run_status",
        create_type=True,
    )

    # -----------------------------------------------------------------------
    # runs table
    # -----------------------------------------------------------------------
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("seq_counter", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # (project_id, created_at DESC) — "latest run for project" queries
    op.execute(
        "CREATE INDEX ix_runs_project_created "
        "ON runs (project_id, created_at DESC)"
    )

    # -----------------------------------------------------------------------
    # Extend nodes
    # -----------------------------------------------------------------------

    # run_id — nullable only because pre-existing rows predate runs
    op.add_column(
        "nodes",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_nodes_run_id", "nodes", "runs",
        ["run_id"], ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "nodes",
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
    )

    # rework lineage: new node points at the rejected node it replaces
    op.add_column(
        "nodes",
        sa.Column("rework_of", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_nodes_rework_of", "nodes", "nodes",
        ["rework_of"], ["id"],
        ondelete="SET NULL",
    )

    # Concurrency safety: lease-based claiming
    op.add_column(
        "nodes",
        sa.Column("claimed_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "nodes",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Drop input_artifact_ids — readiness is resolved from artifact selectors
    # in config['required_inputs'], never from pre-baked artifact UUIDs.
    op.drop_column("nodes", "input_artifact_ids")

    # Scheduler's hot query: find claimable nodes in a given run by status
    op.create_index("ix_nodes_run_status", "nodes", ["run_id", "status"])

    # Rework lineage lookup (partial — only rows that are reworks)
    op.create_index(
        "ix_nodes_rework_of", "nodes", ["rework_of"],
        postgresql_where=sa.text("rework_of IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # Extend artifacts
    # -----------------------------------------------------------------------

    # Semantic kind for selector matching: 'prd', 'api_contract', 'source_code',
    # 'stdout', 'verdict'.  VARCHAR (not enum) so later phases add kinds freely.
    op.add_column(
        "artifacts",
        sa.Column("kind", sa.String(64), nullable=False, server_default="generic"),
    )

    # Which agent role produced this artifact (enables {kind, from_role} selectors)
    op.add_column(
        "artifacts",
        sa.Column("produced_by_role", sa.String(64), nullable=True),
    )

    # Tie artifact to a run — nullable for pre-existing rows
    op.add_column(
        "artifacts",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_artifacts_run_id", "artifacts", "runs",
        ["run_id"], ["id"],
        ondelete="CASCADE",
    )

    # Readiness-check query: find artifacts in a run by kind
    op.create_index("ix_artifacts_run_kind", "artifacts", ["run_id", "kind"])

    # -----------------------------------------------------------------------
    # Artifact immutability trigger
    # -----------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_artifact_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'artifacts are immutable; create a new version instead';
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_artifacts_immutable
        BEFORE UPDATE ON artifacts
        FOR EACH ROW
        EXECUTE FUNCTION prevent_artifact_update();
    """)


def downgrade() -> None:
    # -- Drop immutability trigger and function --
    op.execute("DROP TRIGGER IF EXISTS trg_artifacts_immutable ON artifacts")
    op.execute("DROP FUNCTION IF EXISTS prevent_artifact_update()")

    # -- Revert artifact extensions --
    op.drop_index("ix_artifacts_run_kind", table_name="artifacts")
    op.drop_constraint("fk_artifacts_run_id", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "run_id")
    op.drop_column("artifacts", "produced_by_role")
    op.drop_column("artifacts", "kind")

    # -- Revert node extensions --
    op.drop_index("ix_nodes_rework_of", table_name="nodes")
    op.drop_index("ix_nodes_run_status", table_name="nodes")
    op.drop_column("nodes", "lease_expires_at")
    op.drop_column("nodes", "claimed_by")
    op.drop_constraint("fk_nodes_rework_of", "nodes", type_="foreignkey")
    op.drop_column("nodes", "rework_of")
    op.drop_column("nodes", "attempt")
    op.drop_constraint("fk_nodes_run_id", "nodes", type_="foreignkey")
    op.drop_column("nodes", "run_id")

    # Re-add input_artifact_ids (restored for backward compatibility)
    op.add_column(
        "nodes",
        sa.Column(
            "input_artifact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default="{}",
        ),
    )

    # -- Drop runs table and its index --
    op.drop_index("ix_runs_project_created", table_name="runs")
    op.drop_table("runs")

    # -- Drop run_status enum --
    op.execute("DROP TYPE IF EXISTS run_status")
