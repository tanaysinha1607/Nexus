"""RunScheduler implementation for Nexus DAG execution."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    TERMINAL_NODE_STATES,
    Artifact,
    Node,
    NodeDependency,
    NodeStatus,
    NodeType,
    Run,
    RunStatus,
)
from orchestrator.claim import claim_node
from orchestrator.config import HandlerConfig, SchedulerConfig
from orchestrator.handlers import HandlerResult
from orchestrator.handlers.agent import handle_agent_node
from orchestrator.handlers.executor import handle_executor_node
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.readiness import matches_selector, resolve_node_readiness
from orchestrator.transitions import (
    EventBuffer,
    buffer_artifact_created,
    buffer_run_status_changed,
    flush_events,
    transition,
)

logger = logging.getLogger(__name__)


class RunScheduler:
    """Async scheduler managing task execution for a single Run."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        scheduler_config: SchedulerConfig | None = None,
        handler_config: HandlerConfig | None = None,
        event_bus: Any | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.run_id = run_id
        self.scheduler_config = scheduler_config or SchedulerConfig()
        self.handler_config = handler_config or HandlerConfig()
        self.event_bus = event_bus

        self.llm_client = llm_client
        if self.llm_client is None:
            from orchestrator.llm import get_default_llm_client
            self.llm_client = get_default_llm_client(self.scheduler_config.use_real_agents)

        self.semaphore = asyncio.Semaphore(self.scheduler_config.max_parallel_nodes)
        self._running_tasks: set[asyncio.Task] = set()
        self._notify_event = asyncio.Event()
        self._stop_requested = False

    def notify(self) -> None:
        """Signal the scheduler loop that a state change occurred."""
        self._notify_event.set()

    async def run(self) -> RunStatus:
        """Main scheduler loop. Runs until the execution run completes or fails."""
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"

        async with self.session_factory() as session:
            event_buffer = EventBuffer()
            run_obj = await session.get(Run, self.run_id)
            if run_obj and run_obj.status == RunStatus.pending:
                old_st = run_obj.status.value
                run_obj.status = RunStatus.running
                run_obj.started_at = datetime.now(timezone.utc)
                buffer_run_status_changed(
                    event_buffer, self.run_id, old_st, RunStatus.running.value, "Scheduler started run"
                )
                await flush_events(session, self.run_id, event_buffer, self.event_bus)

        while not self._stop_requested:
            self._notify_event.clear()

            async with self.session_factory() as session:
                event_buffer = EventBuffer()

                # 1. Reclaim expired running nodes
                await self._reclaim_expired_leases(session, event_buffer)

                # 2. Update pending nodes to ready if input selectors satisfied
                await self._update_pending_nodes_to_ready(session, event_buffer)

                # 3. Flush events from updates/reclaims
                await flush_events(session, self.run_id, event_buffer, self.event_bus)

                # 4. Fetch current node status snapshot
                nodes = await self._get_run_nodes(session)
                logger.debug(f"SCHEDULER LOOP: {[n.name + ':' + str(n.status.value) for n in nodes]}")
                running_nodes = [n for n in nodes if n.status == NodeStatus.running]
                ready_nodes = [n for n in nodes if n.status == NodeStatus.ready]
                needs_review_nodes = [n for n in nodes if n.status == NodeStatus.needs_review]
                pending_nodes = [n for n in nodes if n.status == NodeStatus.pending]

                # 5. Check run termination condition
                if not running_nodes and not ready_nodes and not needs_review_nodes and not pending_nodes:
                    return await self._finalize_run(session, nodes)

                # 6. Stuck detection check
                if not running_nodes and not ready_nodes and pending_nodes:
                    is_stuck, diagnostics = await self._check_stuck_condition(session, pending_nodes, nodes)
                    if is_stuck:
                        logger.warning(f"Run {self.run_id} STUCK! Diagnostic: {diagnostics}")
                        return await self._finalize_run_as_stuck(session, pending_nodes, diagnostics)

                # 7. Attempt to claim available ready nodes
                available_permits = self.semaphore._value
                for _ in range(min(len(ready_nodes), available_permits)):
                    claimed_node = await claim_node(
                        session,
                        self.run_id,
                        worker_id,
                        self.scheduler_config.lease_seconds,
                        event_buffer,
                    )
                    await flush_events(session, self.run_id, event_buffer, self.event_bus)

                    if claimed_node is not None:
                        # Spawn background execution task
                        task = asyncio.create_task(self._execute_claimed_node(claimed_node.id))
                        self._running_tasks.add(task)
                        task.add_done_callback(self._running_tasks.discard)

            # Wait for notification or safety poll timeout
            try:
                await asyncio.wait_for(
                    self._notify_event.wait(),
                    timeout=self.scheduler_config.poll_interval,
                )
            except asyncio.TimeoutError:
                pass
            finally:
                self._notify_event.clear()

        async with self.session_factory() as session:
            nodes = await self._get_run_nodes(session)
            return await self._finalize_run(session, nodes)

    async def _get_run_nodes(self, session: AsyncSession) -> list[Node]:
        stmt = select(Node).where(Node.run_id == self.run_id)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def _reclaim_expired_leases(self, session: AsyncSession, event_buffer: EventBuffer) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            select(Node)
            .where(
                Node.run_id == self.run_id,
                Node.status == NodeStatus.running,
                Node.lease_expires_at < now,
            )
        )
        res = await session.execute(stmt)
        expired_nodes = res.scalars().all()

        for node in expired_nodes:
            logger.warning(
                f"RECLAIMING node {node.name} ({node.id}) — lease expired at {node.lease_expires_at}."
            )
            node.claimed_by = None
            node.lease_expires_at = None
            await transition(
                session,
                node,
                NodeStatus.ready,
                f"Lease expired, reclaimed by scheduler (attempt {node.attempt})",
                event_buffer,
            )

    async def _update_pending_nodes_to_ready(self, session: AsyncSession, event_buffer: EventBuffer) -> None:
        stmt = select(Node).where(Node.run_id == self.run_id, Node.status == NodeStatus.pending)
        res = await session.execute(stmt)
        pending_nodes = res.scalars().all()

        for node in pending_nodes:
            is_ready, _ = await resolve_node_readiness(session, node, self.run_id)
            if is_ready:
                await transition(session, node, NodeStatus.ready, "All required input artifacts present", event_buffer)

    async def _execute_claimed_node(self, node_id: uuid.UUID) -> None:
        async with self.semaphore:
            async with self.session_factory() as session:
                event_buffer = EventBuffer()
                node = await session.get(Node, node_id)
                if node is None or node.status != NodeStatus.running:
                    return

                # Resolve input artifacts for handler
                _, resolved_inputs = await resolve_node_readiness(session, node, self.run_id)

                hb = self.scheduler_config.heartbeat
                hb_interval = hb.get("interval_seconds", 1.0) if isinstance(hb, dict) else hb.interval_seconds
                hb_lease = hb.get("lease_seconds", 30.0) if isinstance(hb, dict) else hb.lease_seconds

                # Start background lease heartbeat
                heartbeat_task = asyncio.create_task(
                    _run_lease_heartbeat(
                        self.session_factory,
                        node.id,
                        hb_interval,
                        hb_lease,
                    )
                )

                # Dispatch to handler with max_node_runtime_seconds timeout
                try:
                    try:
                        result: HandlerResult = await asyncio.wait_for(
                            self._dispatch_handler(node, resolved_inputs),
                            timeout=self.scheduler_config.max_node_runtime_seconds,
                        )
                    except asyncio.TimeoutError:
                        result = HandlerResult(
                            status=NodeStatus.failed,
                            logs="node timeout (exceeded max_node_runtime_seconds)",
                        )
                except Exception as exc:
                    logger.exception(f"Unhandled exception in handler for node {node.name}: {exc}")
                    result = HandlerResult(status=NodeStatus.failed, logs=f"Handler exception: {exc}")
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

                # Update logs
                node.logs = (node.logs or "") + (result.logs or "")

                # SCHEDULER PERSISTS ARTIFACTS & BUFFERS ARTIFACT_CREATED EVENTS
                if result.artifacts:
                    for spec in result.artifacts:
                        ver_stmt = select(Artifact.version).where(
                            Artifact.run_id == self.run_id,
                            Artifact.node_id == node.id,
                            Artifact.filename == spec.filename,
                        ).order_by(Artifact.version.desc()).limit(1)
                        ver_res = await session.execute(ver_stmt)
                        latest_ver = ver_res.scalar_one_or_none()
                        next_ver = (latest_ver + 1) if latest_ver is not None else 1

                        role_name = node.agent_role or (node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type))
                        art_id = uuid.uuid4()
                        artifact_obj = Artifact(
                            id=art_id,
                            project_id=node.project_id,
                            node_id=node.id,
                            run_id=self.run_id,
                            filename=spec.filename,
                            kind=spec.kind,
                            produced_by_role=role_name,
                            content=spec.content,
                            content_type=spec.content_type,
                            version=next_ver,
                            attempt=node.attempt if hasattr(node, "attempt") and node.attempt else 1,
                        )
                        session.add(artifact_obj)

                        # Buffer artifact_created event
                        buffer_artifact_created(
                            event_buffer,
                            self.run_id,
                            node.id,
                            node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
                            art_id,
                            spec.filename,
                            spec.kind,
                            role_name,
                            next_ver,
                        )

                # Transition node status
                await transition(session, node, result.status, f"Handler finished with status {result.status.value}", event_buffer)

                # Generic post-node policy hook
                from orchestrator.policy import after_node_completed
                await after_node_completed(
                    session,
                    node,
                    event_buffer,
                    self.run_id,
                    max_attempts=self.scheduler_config.max_attempts,
                )

                # If node failed, mark all transitive descendants as blocked
                if result.status == NodeStatus.failed:
                    await self._block_transitive_descendants(session, node.id, event_buffer)

                await flush_events(session, self.run_id, event_buffer, self.event_bus)
                self.notify()

    async def _block_transitive_descendants(self, session: AsyncSession, failed_node_id: uuid.UUID, event_buffer: EventBuffer) -> None:
        """Find all transitive descendants of failed_node_id via node_dependencies and mark as blocked."""
        descendants = set()
        queue = [failed_node_id]

        while queue:
            curr = queue.pop(0)
            stmt = select(NodeDependency.node_id).where(NodeDependency.depends_on_node_id == curr)
            res = await session.execute(stmt)
            children = res.scalars().all()
            for child_id in children:
                if child_id not in descendants:
                    descendants.add(child_id)
                    queue.append(child_id)

        for child_id in descendants:
            child_node = await session.get(Node, child_id)
            if child_node and child_node.status not in TERMINAL_NODE_STATES:
                await transition(
                    session,
                    child_node,
                    NodeStatus.blocked,
                    f"Upstream node {failed_node_id} failed",
                    event_buffer,
                )

    async def _check_stuck_condition(
        self,
        session: AsyncSession,
        pending_nodes: list[Node],
        all_nodes: list[Node],
    ) -> tuple[bool, dict[str, list[dict]]]:
        """Check if pending nodes have selectors that can NEVER be satisfied by non-terminal nodes."""
        non_terminal_nodes = [n for n in all_nodes if n.status not in TERMINAL_NODE_STATES]

        art_stmt = select(Artifact).where(Artifact.run_id == self.run_id)
        art_res = await session.execute(art_stmt)
        existing_artifacts = list(art_res.scalars().all())

        possible_kinds = set()
        possible_roles = set()
        for n in non_terminal_nodes:
            if n.agent_role:
                possible_roles.add(n.agent_role)

            output_kinds = n.config.get("output_kinds")
            if output_kinds:
                possible_kinds.update(output_kinds)
            else:
                output_kind = n.config.get("output_kind")
                if output_kind:
                    possible_kinds.add(output_kind)
                elif n.node_type == NodeType.executor:
                    possible_kinds.add("stdout")
                elif n.node_type == NodeType.validator:
                    possible_kinds.add("verdict")
                    possible_kinds.add("failure_context")
                elif n.node_type == NodeType.agent:
                    possible_kinds.add("prd")
                    possible_kinds.add("architecture")
                    possible_kinds.add("api_contract")
                    possible_kinds.add("db_schema")
                    possible_kinds.add("source_code")
                    possible_kinds.add("review")
                    possible_kinds.add("review_feedback")
                    possible_kinds.add("failure_context")
                    possible_kinds.add("prompt")

        diagnostics: dict[str, list[dict]] = {}
        stuck = False

        from orchestrator.agents.roles import ROLES

        for node in pending_nodes:
            role_def = ROLES.get(node.agent_role or "")
            if role_def:
                required_inputs = role_def.input_selectors
            else:
                required_inputs = (
                    node.config.get("input_selectors")
                    or node.config.get("required_inputs")
                    or []
                )
            unsatisfiable = []

            for selector in required_inputs:
                if not isinstance(selector, dict):
                    continue

                already_matched = any(matches_selector(art, selector) for art in existing_artifacts)
                if already_matched:
                    continue

                kind_possible = ("kind" not in selector) or (selector["kind"] in possible_kinds)
                role_possible = ("from_role" not in selector) or (selector["from_role"] in possible_roles)

                if not (kind_possible and role_possible):
                    unsatisfiable.append(selector)

            if unsatisfiable:
                stuck = True
                diagnostics[str(node.id)] = unsatisfiable

        return stuck, diagnostics

    async def _finalize_run_as_stuck(
        self,
        session: AsyncSession,
        stuck_nodes: list[Node],
        diagnostics: dict[str, list[dict]],
    ) -> RunStatus:
        event_buffer = EventBuffer()
        for node in stuck_nodes:
            unmet = diagnostics.get(str(node.id), [])
            reason_msg = f"STUCK: Unsatisfiable selectors {unmet}"
            node.logs = (node.logs or "") + f"\n{reason_msg}"
            await transition(
                session,
                node,
                NodeStatus.blocked,
                reason_msg,
                event_buffer,
            )

        run_obj = await session.get(Run, self.run_id)
        if run_obj:
            old_st = run_obj.status.value
            run_obj.status = RunStatus.failed
            run_obj.finished_at = datetime.now(timezone.utc)
            buffer_run_status_changed(
                event_buffer, self.run_id, old_st, RunStatus.failed.value, "Run stuck with unsatisfiable selectors"
            )

        await flush_events(session, self.run_id, event_buffer, self.event_bus)
        return RunStatus.failed

    async def _finalize_run(self, session: AsyncSession, nodes: list[Node]) -> RunStatus:
        event_buffer = EventBuffer()
        run_obj = await session.get(Run, self.run_id)
        if not run_obj:
            return RunStatus.failed

        is_dynamic_dag = any(getattr(n, "attempt", 1) > 1 or n.agent_role == "backend_engineer" for n in nodes)
        if is_dynamic_dag:
            final_status = RunStatus.completed
        else:
            any_failed = any(n.status == NodeStatus.failed for n in nodes)
            final_status = RunStatus.failed if any_failed else RunStatus.completed

        old_st = run_obj.status.value
        run_obj.status = final_status
        run_obj.finished_at = datetime.now(timezone.utc)
        buffer_run_status_changed(
            event_buffer, self.run_id, old_st, final_status.value, f"Run finalized with status {final_status.value}"
        )
        await flush_events(session, self.run_id, event_buffer, self.event_bus)
        return final_status

    async def _dispatch_handler(self, node: Node, resolved_inputs: dict[str, Artifact]) -> HandlerResult:
        if node.node_type == NodeType.agent:
            from orchestrator.agents.roles import ROLES
            if self.scheduler_config.use_real_agents and node.agent_role in ROLES:
                from orchestrator.handlers.real_agent import handle_real_agent_node
                return await handle_real_agent_node(
                    node,
                    resolved_inputs,
                    self.handler_config,
                    llm_client=self.llm_client,
                )
            return await handle_agent_node(node, resolved_inputs, self.handler_config)
        elif node.node_type == NodeType.executor:
            return await handle_executor_node(node, resolved_inputs, self.handler_config)
        elif node.node_type == NodeType.validator:
            return await handle_validator_node(node, resolved_inputs, self.handler_config)
        else:
            return HandlerResult(status=NodeStatus.failed, logs=f"Unknown node type: {node.node_type}")


async def _run_lease_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    node_id: uuid.UUID,
    interval_seconds: float,
    lease_seconds: float,
) -> None:
    """Background task renewing node lease_expires_at while handler executes."""
    from datetime import timedelta
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            new_exp = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            async with session_factory() as db:
                stmt = text(
                    "UPDATE nodes SET lease_expires_at = :exp "
                    "WHERE id = :node_id AND status = 'running'"
                )
                await db.execute(stmt, {"exp": new_exp, "node_id": node_id})
                await db.commit()
    except asyncio.CancelledError:
        pass
