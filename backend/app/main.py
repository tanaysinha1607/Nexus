"""Nexus — orchestration engine for autonomous software engineering.

FastAPI application entry point with shared Redis client lifespan.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import from_url

from app.config import settings
from app.routers import projects, runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for shared application resources (e.g. Redis client)."""
    # Shared Redis connection lifecycle on startup
    redis_client = from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client
    yield
    # Graceful cleanup on shutdown
    await redis_client.aclose()


app = FastAPI(
    title="Nexus",
    description="Orchestration engine for autonomous software engineering",
    version="0.3.0",
    lifespan=lifespan,
)

# CORS — allow the Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(projects.router, prefix="/api")
app.include_router(runs.router)


@app.get("/health")
async def health() -> dict:
    """Simple health-check endpoint."""
    return {"status": "ok", "service": "nexus-backend"}
