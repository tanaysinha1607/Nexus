"""Configuration dataclasses for the Nexus orchestrator.

Injectable timing configuration for schedulers and handlers to ensure testability
without module-level constants.
"""

from dataclasses import dataclass, field


@dataclass
class HeartbeatConfig:
    """Injectable configuration for lease heartbeat renewals."""

    interval_seconds: float = 20.0
    lease_seconds: float = 60.0


@dataclass
class SchedulerConfig:
    """Injectable configuration for the RunScheduler."""

    max_parallel_nodes: int = 4
    lease_seconds: float = 60.0
    poll_interval: float = 1.0
    max_node_runtime_seconds: float = 600.0
    use_real_agents: bool = True
    max_attempts: int = 5
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)

    def __post_init__(self) -> None:
        if self.use_real_agents and self.max_parallel_nodes > 1:
            self.max_parallel_nodes = 1


@dataclass
class HandlerConfig:
    """Injectable configuration for node execution handlers."""

    agent_sleep_range: tuple[float, float] = field(default=(0.5, 2.0))
    executor_sleep_range: tuple[float, float] = field(default=(0.5, 2.0))
