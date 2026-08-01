"""Kahn's algorithm DAG cycle detection for Nexus task graphs."""

from collections import deque
from typing import Any, Sequence


def detect_dag_cycle(
    node_ids: Sequence[Any],
    edges: Sequence[tuple[Any, Any]],  # (node_id, depends_on_node_id) -> node_id depends on depends_on_node_id
) -> tuple[bool, list[Any]]:
    """Detect cycles in a directed graph using Kahn's algorithm.

    Args:
        node_ids: List of all node identifiers in the graph.
        edges: List of tuples (node_id, depends_on_node_id) representing dependency edges.
               node_id depends on depends_on_node_id.

    Returns:
        tuple[bool, list[Any]]:
            - is_valid (True if no cycle exists, False if cycle detected)
            - cycle_nodes (list of node IDs participating in or blocked by the cycle)
    """
    nodes_set = set(node_ids)
    if not nodes_set:
        return True, []

    # in_degree count: number of incoming dependency edges (dependencies node depends on)
    in_degree = {n: 0 for n in nodes_set}
    # adjacency list: u -> v means v depends on u (u completing allows v to proceed)
    dependents_map = {n: set() for n in nodes_set}

    for u, v in edges:
        # u depends on v: v -> u edge in dependency flow
        if u in nodes_set and v in nodes_set:
            in_degree[u] += 1
            dependents_map[v].add(u)

    # Queue of nodes with 0 incoming dependencies
    queue = deque([n for n in nodes_set if in_degree[n] == 0])
    processed_count = 0

    while queue:
        curr = queue.popleft()
        processed_count += 1

        for dependent in dependents_map[curr]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if processed_count == len(nodes_set):
        return True, []

    # Cycle detected: nodes with in_degree > 0
    cycle_nodes = [n for n in nodes_set if in_degree[n] > 0]
    return False, cycle_nodes
