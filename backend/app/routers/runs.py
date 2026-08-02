"""API routes for runs and WebSocket event streaming."""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.event_bus import RedisEventBus
from app.models import Artifact, Node, NodeDependency, NodeType, Project, Run
from app.schemas import EdgeOut, RunOut, RunSnapshotOut
from orchestrator import RunScheduler, detect_dag_cycle
from app.ws import handle_ws_subscription

router = APIRouter(tags=["runs"])

# Active scheduler tasks
active_schedulers: dict[uuid.UUID, RunScheduler] = {}


def build_seed_graph_specs(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    fail_executor: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    """Construct specifications for the 7-node Seed Graph with parallel branch.

    Nodes:
      0: PM (agent, role='product_manager') -> emits kind='prd'
      1: Architect (agent, role='architect') -> requires {kind:'prd'}, emits kind='api_contract' & kind='db_schema'
      2: Backend (agent, role='backend_engineer') -> requires {kind:'api_contract'}, emits kind='source_code'
      3: BackendExecutor (executor, role='backend_executor') -> requires {kind:'source_code'}, emits kind='stdout' (exit_code=0 default, exit_code=1 if fail_executor=True)
      4: DBSetup (executor, role='db_executor') -> requires {kind:'db_schema'}, emits kind='stdout'
      5: Validator (validator, role='validator') -> requires {kind:'stdout', from_role:'backend_executor'}, emits kind='verdict'
      6: Reviewer (agent, role='code_reviewer') -> requires {kind:'verdict'}, emits kind='review_summary'
    """
    node_specs = [
        {
            "name": "PM",
            "node_type": NodeType.agent,
            "agent_role": "product_manager",
            "config": {"output_kind": "prd", "required_inputs": []},
        },
        {
            "name": "Architect",
            "node_type": NodeType.agent,
            "agent_role": "architect",
            "config": {
                "output_kinds": ["api_contract", "db_schema"],
                "required_inputs": [{"kind": "prd"}],
            },
        },
        {
            "name": "Backend",
            "node_type": NodeType.agent,
            "agent_role": "backend_engineer",
            "config": {
                "output_kind": "source_code",
                "required_inputs": [{"kind": "api_contract"}],
            },
        },
        {
            "name": "BackendExecutor",
            "node_type": NodeType.executor,
            "agent_role": "backend_executor",
            "config": {
                "exit_code": 1 if fail_executor else 0,
                "required_inputs": [{"kind": "source_code"}],
            },
        },
        {
            "name": "DBSetup",
            "node_type": NodeType.executor,
            "agent_role": "db_executor",
            "config": {
                "exit_code": 0,
                "required_inputs": [{"kind": "db_schema"}],
            },
        },
        {
            "name": "Validator",
            "node_type": NodeType.validator,
            "agent_role": "validator",
            "config": {
                "required_inputs": [{"kind": "stdout", "from_role": "backend_executor"}],
            },
        },
        {
            "name": "Reviewer",
            "node_type": NodeType.agent,
            "agent_role": "code_reviewer",
            "config": {
                "output_kind": "review_summary",
                "required_inputs": [{"kind": "verdict"}],
            },
        },
    ]

    edges = [
        (1, 0),  # Architect depends on PM
        (2, 1),  # Backend depends on Architect
        (3, 2),  # BackendExecutor depends on Backend
        (4, 1),  # DBSetup depends on Architect (PARALLEL BRANCH)
        (5, 3),  # Validator depends on BackendExecutor
        (6, 5),  # Reviewer depends on Validator
    ]

    return node_specs, edges


@router.post("/api/projects/{project_id}/runs", response_model=RunOut, status_code=201)
async def create_run(
    project_id: uuid.UUID,
    request: Request,
    fail_executor: bool = Query(False, description="Set BackendExecutor to exit_code=1 for testing failing verdict"),
    graph: str = Query("default", description="Graph structure: default (7 nodes) or pm_only (1 PM node)"),
    db: AsyncSession = Depends(get_db),
) -> Run:
    """Create and start a new execution run for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    run_id = uuid.uuid4()

    # Seed artifact containing project user_prompt (node_id = None for system inputs)
    seed_prompt_artifact = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=None,
        run_id=run_id,
        filename="user_prompt.txt",
        kind="user_prompt",
        produced_by_role="system",
        content=project.user_prompt,
        version=1,
        attempt=1,
    )

    if graph == "pm_only":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        nodes = [pm_node]
        edge_pairs = []
    elif graph == "pm_arch":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        nodes = [pm_node, arch_node, api_node]
        edge_pairs = []
    elif graph == "pm_arch_backend":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
    elif graph == "pm_arch_backend_exec":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        exec_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="BackendExecutor",
            node_type=NodeType.executor,
            agent_role="backend_executor",
            config={"required_inputs": [{"kind": "source_code"}]},
        )
        val_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Validator",
            node_type=NodeType.validator,
            agent_role="validator",
            config={"required_inputs": [{"kind": "execution_report"}]},
        )
        reviewer_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Reviewer",
            node_type=NodeType.agent,
            agent_role="senior_reviewer",
            config={
                "required_inputs": [
                    {"kind": "verdict"},
                    {"kind": "source_code"},
                    {"kind": "api_contract"},
                ]
            },
        )
        nodes = [pm_node, arch_node, api_node, backend_node, exec_node, val_node, reviewer_node]
        edge_pairs = [
            (pm_node.id, arch_node.id),
            (arch_node.id, api_node.id),
            (api_node.id, backend_node.id),
            (backend_node.id, exec_node.id),
            (exec_node.id, val_node.id),
            (val_node.id, reviewer_node.id),
        ]
    elif graph == "pm_arch_backend_qa":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        qa_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="QA",
            node_type=NodeType.agent,
            agent_role="qa_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        exec_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="TestExecutor",
            node_type=NodeType.executor,
            agent_role="test_executor",
            config={"required_inputs": [{"kind": "source_code"}, {"kind": "test_code"}]},
        )
        val_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="TestValidator",
            node_type=NodeType.validator,
            agent_role="test_validator",
            config={"required_inputs": [{"kind": "test_report"}]},
        )
        reviewer_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Reviewer",
            node_type=NodeType.agent,
            agent_role="senior_reviewer",
            config={
                "required_inputs": [
                    {"kind": "verdict"},
                    {"kind": "source_code"},
                    {"kind": "api_contract"},
                ]
            },
        )
        nodes = [pm_node, arch_node, api_node, backend_node, qa_node, exec_node, val_node, reviewer_node]
        edge_pairs = [
            (pm_node.id, arch_node.id),
            (arch_node.id, api_node.id),
            (api_node.id, backend_node.id),
            (api_node.id, qa_node.id),
            (backend_node.id, exec_node.id),
            (qa_node.id, exec_node.id),
            (exec_node.id, val_node.id),
            (val_node.id, reviewer_node.id),
        ]
    elif graph == "pm_arch_frontend":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        frontend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Frontend",
            node_type=NodeType.agent,
            agent_role="frontend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        exec_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="BuildExecutor",
            node_type=NodeType.executor,
            agent_role="build_executor",
            config={"required_inputs": [{"kind": "frontend_code"}]},
        )
        val_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="BuildValidator",
            node_type=NodeType.validator,
            agent_role="build_validator",
            config={"required_inputs": [{"kind": "build_report"}]},
        )
        reviewer_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Reviewer",
            node_type=NodeType.agent,
            agent_role="senior_reviewer",
            config={
                "required_inputs": [
                    {"kind": "verdict"},
                    {"kind": "api_contract"},
                ]
            },
        )
        nodes = [pm_node, arch_node, api_node, frontend_node, exec_node, val_node, reviewer_node]
        edge_pairs = [
            (pm_node.id, arch_node.id),
            (arch_node.id, api_node.id),
            (api_node.id, frontend_node.id),
            (frontend_node.id, exec_node.id),
            (exec_node.id, val_node.id),
            (val_node.id, reviewer_node.id),
        ]
    elif graph == "pm_arch_backend_security":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        exec_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="SecurityScanExecutor",
            node_type=NodeType.executor,
            agent_role="security_executor",
            config={"required_inputs": [{"kind": "source_code"}]},
        )
        val_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="SecurityValidator",
            node_type=NodeType.validator,
            agent_role="security_validator",
            config={"required_inputs": [{"kind": "security_report"}]},
        )
        reviewer_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Reviewer",
            node_type=NodeType.agent,
            agent_role="senior_reviewer",
            config={
                "required_inputs": [
                    {"kind": "verdict"},
                    {"kind": "source_code"},
                    {"kind": "api_contract"},
                ]
            },
        )
        nodes = [pm_node, arch_node, api_node, backend_node, exec_node, val_node, reviewer_node]
        edge_pairs = [
            (pm_node.id, arch_node.id),
            (arch_node.id, api_node.id),
            (api_node.id, backend_node.id),
            (backend_node.id, exec_node.id),
            (exec_node.id, val_node.id),
            (val_node.id, reviewer_node.id),
        ]
    elif graph == "pm_arch_backend_devops":
        pm_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        devops_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="DevOps",
            node_type=NodeType.agent,
            agent_role="devops_engineer",
            config={"required_inputs": [{"kind": "source_code"}]},
        )
        exec_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="DevOpsExecutor",
            node_type=NodeType.executor,
            agent_role="devops_executor",
            config={"required_inputs": [{"kind": "dockerfile"}, {"kind": "source_code"}]},
        )
        val_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="DevOpsValidator",
            node_type=NodeType.validator,
            agent_role="devops_validator",
            config={"required_inputs": [{"kind": "devops_report"}]},
        )
        reviewer_node = Node(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name="Reviewer",
            node_type=NodeType.agent,
            agent_role="senior_reviewer",
            config={
                "required_inputs": [
                    {"kind": "verdict"},
                    {"kind": "source_code"},
                    {"kind": "api_contract"},
                ]
            },
        )
        nodes = [pm_node, arch_node, api_node, backend_node, devops_node, exec_node, val_node, reviewer_node]
        edge_pairs = [
            (pm_node.id, arch_node.id),
            (arch_node.id, api_node.id),
            (api_node.id, backend_node.id),
            (backend_node.id, devops_node.id),
            (devops_node.id, exec_node.id),
            (exec_node.id, val_node.id),
            (val_node.id, reviewer_node.id),
        ]
    else:
        node_specs, raw_edges = build_seed_graph_specs(project_id, run_id, fail_executor=fail_executor)

        nodes = []
        temp_id_map: dict[int, uuid.UUID] = {}
        for idx, spec in enumerate(node_specs):
            n_id = uuid.uuid4()
            temp_id_map[idx] = n_id
            node_obj = Node(
                id=n_id,
                project_id=project_id,
                run_id=run_id,
                name=spec["name"],
                node_type=spec["node_type"],
                agent_role=spec.get("agent_role"),
                config=spec.get("config", {}),
            )
            nodes.append(node_obj)

        edge_pairs = [(temp_id_map[u], temp_id_map[v]) for u, v in raw_edges]

    # Kahn's Cycle Detection
    node_ids = [n.id for n in nodes]
    is_valid, cycle_members = detect_dag_cycle(node_ids, edge_pairs)
    if not is_valid:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Cyclic graph detected",
                "cycle_members": [str(c) for c in cycle_members],
            },
        )

    # Insert Run, Seed Artifact, Nodes, Edges
    run_obj = Run(id=run_id, project_id=project_id)
    db.add(run_obj)
    db.add(seed_prompt_artifact)
    db.add_all(nodes)
    await db.flush()

    for u_id, v_id in edge_pairs:
        dep = NodeDependency(node_id=u_id, depends_on_node_id=v_id)
        db.add(dep)

    await db.commit()
    await db.refresh(run_obj)

    # Use RedisEventBus with shared redis client from app.state.redis if request is provided
    event_bus = RedisEventBus(request.app.state.redis) if request is not None and hasattr(request, "app") else None

    scheduler = RunScheduler(
        session_factory=async_session,
        run_id=run_id,
        event_bus=event_bus,
    )
    active_schedulers[run_id] = scheduler
    asyncio.create_task(scheduler.run())

    return run_obj


@router.get("/api/runs/{run_id}/snapshot", response_model=RunSnapshotOut)
async def get_run_snapshot(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve full snapshot of a run: nodes, edges, artifacts, and sequence counter."""
    run_obj = await db.get(Run, run_id)
    if not run_obj:
        raise HTTPException(status_code=404, detail="Run not found")

    nodes_stmt = select(Node).where(Node.run_id == run_id)
    nodes_res = await db.execute(nodes_stmt)
    nodes = list(nodes_res.scalars().all())

    node_ids = [n.id for n in nodes]
    edges = []
    if node_ids:
        edges_stmt = select(NodeDependency).where(NodeDependency.node_id.in_(node_ids))
        edges_res = await db.execute(edges_stmt)
        edges = list(edges_res.scalars().all())

    artifacts_stmt = select(Artifact).where(Artifact.run_id == run_id)
    artifacts_res = await db.execute(artifacts_stmt)
    artifacts = list(artifacts_res.scalars().all())

    return {
        "run": run_obj,
        "nodes": nodes,
        "edges": edges,
        "artifacts": artifacts,
        "seq_counter": run_obj.seq_counter,
    }


@router.websocket("/ws/runs/{run_id}")
async def run_events_websocket(websocket: WebSocket, run_id: uuid.UUID):
    """WebSocket endpoint streaming live transition events for a run via Redis Pub/Sub."""
    redis_client = websocket.app.state.redis
    await handle_ws_subscription(websocket, run_id, redis_client)
