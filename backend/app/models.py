"""SQLAlchemy ORM models for the Nexus task-graph orchestration engine.

Three distinct node types enforce the core design principle:
  - AGENT:     Calls an LLM, produces subjective artifacts (PRDs, code, reviews).
  - EXECUTOR:  Runs a real command (Docker, pytest, ruff) — no LLM involved.
  - VALIDATOR: Applies a deterministic rule to an executor's output — no LLM, no judgment.

Nothing is "passed" unless a real EXECUTOR + VALIDATOR chain produced that verdict.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship, backref


class Base(DeclarativeBase):
    """Declarative base for all Nexus models."""
    pass


# ---------------------------------------------------------------------------
# Enums & Constants
# ---------------------------------------------------------------------------

class ProjectStatus(str, enum.Enum):
    """Lifecycle status of a project."""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class RunStatus(str, enum.Enum):
    """Lifecycle status of a single execution run."""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class NodeType(str, enum.Enum):
    """The three fundamental node types in Nexus.

    AGENT:     LLM-powered, produces subjective artifacts.
    EXECUTOR:  Runs a real process (Docker, shell) — no LLM.
    VALIDATOR: Deterministic pass/fail on an executor's output — no LLM.
    """
    agent = "agent"
    executor = "executor"
    validator = "validator"


class NodeStatus(str, enum.Enum):
    """Lifecycle status of a single node in the DAG."""
    pending = "pending"        # waiting on upstream dependencies
    ready = "ready"            # all deps satisfied, eligible to run
    running = "running"        # currently executing
    needs_review = "needs_review"  # agent output awaiting review
    failed = "failed"          # terminal failure
    completed = "completed"    # successfully finished
    blocked = "blocked"        # upstream dependency failed/blocked
    cancelled = "cancelled"    # run was cancelled


TERMINAL_NODE_STATES = {
    NodeStatus.completed,
    NodeStatus.failed,
    NodeStatus.blocked,
    NodeStatus.cancelled,
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Project(Base):
    """A top-level orchestration project created from a user prompt."""

    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    user_prompt = Column(Text, nullable=False)
    status = Column(
        ENUM(ProjectStatus, name="project_status", create_type=False),
        nullable=False,
        default=ProjectStatus.pending,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    nodes = relationship("Node", back_populates="project", cascade="all, delete-orphan")
    runs = relationship("Run", back_populates="project", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Project {self.name!r} ({self.status.value})>"


class Run(Base):
    """A single execution run of a project."""

    __tablename__ = "runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(
        ENUM(RunStatus, name="run_status", create_type=False),
        nullable=False,
        default=RunStatus.pending,
    )
    seq_counter = Column(BigInteger, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project = relationship("Project", back_populates="runs")
    nodes = relationship("Node", back_populates="run", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Run {self.id} ({self.status.value})>"


class Node(Base):
    """A single node in the project's task DAG.

    The node_type column enforces the AGENT / EXECUTOR / VALIDATOR
    trichotomy at the schema level.  agent_role is only meaningful
    for AGENT nodes (e.g. "product_manager", "backend_engineer").

    Readiness Contract:
      A node is ready iff every selector in config['required_inputs'] resolves to an
      existing artifact row in the same run. Parent node status is NOT a readiness gate.
      node_dependencies exists for cycle detection, blocked-propagation, and visualization.

    Selector Shape:
      {"kind": str, "from_role": str|None, "filename": str|None}
      At least one field must be present; all present fields must match.
    """

    __tablename__ = "nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    node_type = Column(
        ENUM(NodeType, name="node_type", create_type=False),
        nullable=False,
    )
    agent_role = Column(String(100), nullable=True)  # only for agent nodes
    status = Column(
        ENUM(NodeStatus, name="node_status", create_type=False),
        nullable=False,
        default=NodeStatus.pending,
    )
    attempt = Column(Integer, nullable=False, default=1)
    rework_of_id = Column(
        "rework_of",
        UUID(as_uuid=True),
        ForeignKey("nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    claimed_by = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    config = Column(JSONB, default=dict)  # executor: command; validator: rule; config['required_inputs']
    logs = Column(Text, default="")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project = relationship("Project", back_populates="nodes")
    run = relationship("Run", back_populates="nodes")
    produced_artifacts = relationship("Artifact", back_populates="producing_node")

    # Rework self-referential lineage
    rework_of = relationship(
        "Node",
        remote_side=[id],
        backref=backref("reworked_by", cascade="all, delete-orphan"),
    )

    # Dependency edges (many-to-many self-referential)
    depends_on = relationship(
        "Node",
        secondary="node_dependencies",
        primaryjoin="Node.id == NodeDependency.node_id",
        secondaryjoin="Node.id == NodeDependency.depends_on_node_id",
        backref="dependents",
    )

    def __repr__(self) -> str:
        return f"<Node {self.name!r} type={self.node_type.value} ({self.status.value})>"


class Artifact(Base):
    """An output produced by a node — the sole communication channel between nodes.

    Agents, executors, and validators all produce artifacts.
    The orchestrator routes work based on artifact existence, not on
    which agent or LLM produced them.
    """

    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(500), nullable=False)
    kind = Column(String(64), nullable=False, default="generic")
    produced_by_role = Column(String(64), nullable=True)
    content = Column(Text, nullable=False, default="")
    content_type = Column(String(100), nullable=False, default="text/plain")
    version = Column(Integer, nullable=False, default=1)
    attempt = Column(Integer, nullable=False, default=1)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project = relationship("Project", back_populates="artifacts")
    run = relationship("Run", back_populates="artifacts")
    producing_node = relationship("Node", back_populates="produced_artifacts")

    def __repr__(self) -> str:
        return f"<Artifact {self.filename!r} v{self.version}>"


class NodeDependency(Base):
    """Explicit DAG edge: node_id depends on depends_on_node_id.

    The scheduler uses these edges to determine which nodes are ready
    to execute (all upstream dependencies completed).
    """

    __tablename__ = "node_dependencies"

    node_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_node_id = Column(
        UUID(as_uuid=True),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        UniqueConstraint("node_id", "depends_on_node_id", name="uq_node_dependency"),
    )
