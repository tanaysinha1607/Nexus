"""API routes for project management."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Project
from app.schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Create a new orchestration project from a user prompt.

    This is the entry point for the entire Nexus pipeline.
    In later phases, creating a project will also trigger the
    orchestrator to build and schedule the task DAG.
    """
    project = Project(
        name=body.name,
        user_prompt=body.user_prompt,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project
