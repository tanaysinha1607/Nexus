export interface NodeData {
  id: string;
  project_id: string;
  run_id: string;
  name: string;
  node_type: "agent" | "executor" | "validator" | string;
  agent_role: string | null;
  status: "pending" | "ready" | "running" | "completed" | "failed" | "blocked" | string;
  attempt: number;
  rework_of_id: string | null;
  claimed_by: string | null;
  config: Record<string, any>;
  logs: string;
  created_at: string;
  updated_at: string;
}

export interface ArtifactData {
  id: string;
  project_id: string;
  node_id: string | null;
  run_id: string;
  filename: string;
  kind: string;
  produced_by_role: string | null;
  content: string;
  content_type: string;
  version: number;
  attempt: number;
  created_at: string;
}

export interface EdgeData {
  node_id: string;
  depends_on_node_id: string;
}

export interface RunData {
  id: string;
  project_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  seq_counter: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface RunSnapshot {
  run: RunData;
  nodes: NodeData[];
  edges: EdgeData[];
  artifacts: ArtifactData[];
  seq_counter: number;
}

export interface NexusEvent {
  type: "node_status_changed" | "artifact_created" | "run_status_changed" | string;
  run_id: string;
  seq: number;
  ts: string;
  node_id: string | null;
  node_type: string | null;
  old_status?: string;
  new_status?: string;
  reason?: string;
  artifact_id?: string;
  filename?: string;
  kind?: string;
  produced_by_role?: string;
  version?: number;
}
