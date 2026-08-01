"""Pytest configuration and shared fixtures for Nexus backend tests."""

import os
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Project

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus_dev@postgres:5432/nexus",
)


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def session_factory(test_engine):
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture(scope="function")
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession):
    project = Project(name="Test Suite Project", user_prompt="Run pytest suite")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project
