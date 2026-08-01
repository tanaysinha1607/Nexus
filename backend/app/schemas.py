"""Pydantic schemas for API request/response serialization."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import NodeStatus, NodeType, ProjectStatus, RunStatus


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    """Request body for POST /projects."""
    name: str = Field(..., min_length=1, max_length=255)
    user_prompt: str = Field(..., min_length=1)


class ProjectOut(BaseModel):
    """Response body for a project."""
    id: uuid.UUID
    name: str
    user_prompt: str
    status: ProjectStatus
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class RunOut(BaseModel):
    """Response body for a run."""
    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    seq_counter: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class NodeOut(BaseModel):
    """Response body for a node."""
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    name: str
    node_type: NodeType
    agent_role: str | None
    status: NodeStatus
    attempt: int
    rework_of_id: uuid.UUID | None
    claimed_by: str | None
    lease_expires_at: datetime | None
    config: dict
    logs: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

class ArtifactOut(BaseModel):
    """Response body for an artifact."""
    id: uuid.UUID
    project_id: uuid.UUID
    node_id: uuid.UUID | None
    run_id: uuid.UUID
    filename: str
    kind: str
    produced_by_role: str | None
    content: str
    content_type: str
    version: int
    attempt: int = 1
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Dependency Edge
# ---------------------------------------------------------------------------

class EdgeOut(BaseModel):
    """Response body for a DAG dependency edge."""
    node_id: uuid.UUID
    depends_on_node_id: uuid.UUID

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Run Snapshot
# ---------------------------------------------------------------------------

class RunSnapshotOut(BaseModel):
    """Response body for GET /api/runs/{id}/snapshot."""
    run: RunOut
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    artifacts: list[ArtifactOut]
    seq_counter: int
