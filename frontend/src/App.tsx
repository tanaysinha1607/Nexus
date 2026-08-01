import { useEffect, useRef, useState } from "react";
import { DagCanvas } from "./components/DagCanvas";
import { LiveActivityFeed } from "./components/LiveActivityFeed";
import { NodeInspectionPanel } from "./components/NodeInspectionPanel";
import type { ArtifactData, EdgeData, NexusEvent, NodeData, RunData } from "./types";

const CANONICAL_PROMPT =
  "Build a cryptocurrency paper trading platform with authentication, a dashboard, charts, portfolio management, and an admin panel.";

export default function App() {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<string>(CANONICAL_PROMPT);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunData | null>(null);
  const [nodes, setNodes] = useState<NodeData[]>([]);
  const [edges, setEdges] = useState<EdgeData[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactData[]>([]);
  const [events, setEvents] = useState<NexusEvent[]>([]);
  const [selectedNode, setSelectedNode] = useState<NodeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [seqCounter, setSeqCounter] = useState(0);
  const [wsConnected, setWsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const snapshotSeqRef = useRef<number>(0);

  // Fetch or initialize project on mount, and check URL for runId
  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    const paramRunId = searchParams.get("runId");
    if (paramRunId) {
      setRunId(paramRunId);
    }

    async function initProject() {
      try {
        const res = await fetch("http://localhost:8000/api/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: "Phase 1.5 Real DAG Inspection Project",
            user_prompt: prompt,
          }),
        });
        if (res.ok) {
          const data = await res.json();
          setProjectId(data.id);
        }
      } catch (err) {
        console.error("Failed to initialize project", err);
      }
    }
    initProject();
  }, []);

  // Snapshot fetching logic
  const fetchSnapshot = async (id: string) => {
    try {
      const res = await fetch(`http://localhost:8000/api/runs/${id}/snapshot`);
      if (!res.ok) return;
      const data = await res.json();
      setRun(data.run);
      setNodes(data.nodes);
      setEdges(data.edges || []);
      setArtifacts(data.artifacts || []);
      setSeqCounter(data.seq_counter || 0);
      snapshotSeqRef.current = data.seq_counter || 0;

      // Update selected node reference if open
      if (selectedNode) {
        const updated = data.nodes.find((n: NodeData) => n.id === selectedNode.id);
        if (updated) setSelectedNode(updated);
      }
    } catch (err) {
      console.error("Error fetching snapshot", err);
    }
  };

  // WebSocket Connection with Reconnect Contract
  useEffect(() => {
    if (!runId) return;

    fetchSnapshot(runId);

    const wsUrl = `ws://${window.location.hostname}:8000/ws/runs/${runId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
    };

    ws.onclose = () => {
      setWsConnected(false);
    };

    ws.onerror = () => {
      setWsConnected(false);
    };

    ws.onmessage = (evt) => {
      try {
        const eventData: NexusEvent = JSON.parse(evt.data);

        // Reconnect contract: discard events with seq <= snapshot seq
        if (eventData.seq <= snapshotSeqRef.current) {
          return;
        }

        setEvents((prev) => [eventData, ...prev]);
        setSeqCounter(eventData.seq);

        // Handle live state transitions
        if (eventData.type === "node_status_changed" && eventData.node_id) {
          setNodes((prevNodes) =>
            prevNodes.map((n) =>
              n.id === eventData.node_id ? { ...n, status: eventData.new_status || n.status } : n
            )
          );
        } else if (eventData.type === "run_status_changed") {
          setRun((prev) => (prev ? { ...prev, status: eventData.new_status || prev.status } : prev));
        }

        // Fetch full snapshot to get new nodes (rework subchains) & artifact content
        fetchSnapshot(runId);
      } catch (err) {
        console.error("Error parsing WS event", err);
      }
    };

    return () => {
      ws.close();
    };
  }, [runId]);

  // Project Creation & Run Trigger Handler
  const handleCreateProjectAndRun = async () => {
    setLoading(true);
    setEvents([]);
    setSelectedNode(null);
    try {
      // 1. Create project
      const projRes = await fetch("http://localhost:8000/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Crypto Paper Trading Platform",
          user_prompt: prompt,
        }),
      });
      const projData = await projRes.json();
      setProjectId(projData.id);

      // 2. Trigger run on graph=pm_arch_backend_exec
      const runRes = await fetch(
        `http://localhost:8000/api/projects/${projData.id}/runs?graph=pm_arch_backend_exec`,
        { method: "POST" }
      );
      const runData = await runRes.json();
      setRunId(runData.id);
      setRun(runData);
    } catch (err) {
      console.error("Error launching project & run", err);
    } finally {
      setLoading(false);
    }
  };

  // Helper metrics calculation
  const backendAttempts = Math.max(
    1,
    ...nodes.filter((n) => n.agent_role === "backend_engineer").map((n) => n.attempt || 1)
  );

  // Latest attempt verdict & review evaluation
  const verdictArtifacts = artifacts.filter((a) => a.kind === "verdict");
  const latestVerdict = verdictArtifacts.reduce<ArtifactData | null>((latest, curr) => {
    if (!latest) return curr;
    return (curr.attempt || 1) >= (latest.attempt || 1) ? curr : latest;
  }, null);

  const reviewArtifacts = artifacts.filter((a) => a.kind === "review" || a.filename === "review.md");
  const latestReview = reviewArtifacts.reduce<ArtifactData | null>((latest, curr) => {
    if (!latest) return curr;
    return (curr.attempt || 1) >= (latest.attempt || 1) ? curr : latest;
  }, null);

  let verdictResult: "PASS" | "FAIL" | null = null;
  if (latestVerdict) {
    let validatorPassed = false;
    try {
      const parsed = JSON.parse(latestVerdict.content);
      validatorPassed = Boolean(parsed.passed);
    } catch {
      validatorPassed = latestVerdict.content.toLowerCase().includes("pass");
    }

    if (!validatorPassed) {
      verdictResult = "FAIL";
    } else if (latestReview) {
      const isApproved = latestReview.content.toLowerCase().includes("review_verdict: approved");
      verdictResult = isApproved ? "PASS" : "FAIL";
    } else {
      verdictResult = "PASS";
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8 font-sans">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-gray-800 pb-6">
          <div>
            <h1 className="text-3xl font-black bg-gradient-to-r from-purple-400 via-cyan-400 to-emerald-400 bg-clip-text text-transparent tracking-tight">
              Nexus Orchestration Engine
            </h1>
            <p className="text-gray-400 text-sm mt-1 flex items-center gap-2">
              <span className="px-2 py-0.5 rounded bg-purple-950 text-purple-300 font-mono text-xs font-bold border border-purple-800/60">
                Phase 1.5
              </span>
              <span>Real Single-Project Inspection &amp; Self-Healing Pipeline</span>
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-gray-500">Graph: pm_arch_backend_exec</span>
          </div>
        </div>

        {/* Project Form & Run Trigger */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-2xl p-6 space-y-4 shadow-xl">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-gray-200 flex items-center gap-2">
              <span>Project Specification &amp; Run Trigger</span>
              <span className="text-xs font-mono font-normal text-cyan-400">Canonical Prompt</span>
            </h2>
            {projectId && (
              <span className="text-xs font-mono text-gray-500">
                Project ID: <strong className="text-cyan-300">{projectId.slice(0, 8)}...</strong>
              </span>
            )}
          </div>

          <div className="space-y-3">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              className="w-full bg-gray-950 border border-gray-800 rounded-xl p-3 text-xs font-mono text-gray-200 focus:outline-none focus:border-cyan-500 transition leading-relaxed"
              placeholder="Enter project prompt..."
            />

            <div className="flex items-center justify-between gap-4">
              <div className="text-xs font-mono text-gray-400">
                Target DAG: <strong className="text-purple-300">PM → Architect → ApiDesigner → Backend → Executor → Validator</strong>
              </div>

              <button
                onClick={handleCreateProjectAndRun}
                disabled={loading}
                className="px-6 py-3 rounded-xl bg-gradient-to-r from-purple-600 via-cyan-600 to-emerald-600 hover:from-purple-500 hover:to-emerald-500 text-white font-bold text-sm shadow-xl shadow-purple-950/50 disabled:opacity-50 transition cursor-pointer flex items-center gap-2"
              >
                {loading ? (
                  <span>🚀 Launching Execution Run...</span>
                ) : (
                  <span>🚀 Trigger pm_arch_backend_exec Run</span>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Run-Level Banner & Verdict Outcome */}
        {run && (
          <div className="bg-gray-900/90 border border-gray-800 rounded-2xl p-6 flex flex-wrap items-center justify-between gap-6 shadow-xl">
            <div className="space-y-1.5">
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono uppercase text-gray-500">Run ID</span>
                <span className="font-mono text-xs text-cyan-300 font-bold">{run.id}</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span
                  className={`w-2.5 h-2.5 rounded-full ${
                    wsConnected ? "bg-emerald-400 animate-ping" : "bg-amber-400"
                  }`}
                />
                <span className="font-mono text-gray-300">
                  {wsConnected
                    ? "WebSocket Connected (Redis PubSub Live Stream)"
                    : "Reconnecting WS..."}
                </span>
              </div>
            </div>

            {/* Banner Metrics */}
            <div className="flex items-center gap-6">
              {/* Attempt Counter */}
              <div className="text-right border-r border-gray-800 pr-6">
                <div className="text-[10px] font-mono text-gray-400 uppercase font-semibold">Backend Attempts</div>
                <div className="text-lg font-bold font-mono text-amber-400">
                  {backendAttempts} of 5
                </div>
              </div>

              {/* Work Verdict Badge */}
              <div className="flex items-center gap-3">
                <div
                  className={`px-4 py-2 rounded-xl border text-xs font-mono font-extrabold uppercase tracking-wider ${
                    run.status === "completed"
                      ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                      : run.status === "running"
                      ? "bg-amber-500/20 text-amber-300 border-amber-500/50 animate-pulse"
                      : "bg-rose-500/20 text-rose-300 border-rose-500/40"
                  }`}
                >
                  Run: {run.status}
                </div>

                {verdictResult && (
                  <div
                    className={`px-4 py-2 rounded-xl border text-xs font-mono font-black uppercase tracking-wider shadow-lg ${
                      verdictResult === "PASS"
                        ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/50 shadow-emerald-950"
                        : "bg-rose-600 text-white border-rose-500 shadow-rose-950 animate-pulse"
                    }`}
                  >
                    Checks: {verdictResult === "PASS" ? "PASSED" : "FAILED"}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* DAG Visualization */}
        {nodes.length > 0 && (
          <DagCanvas
            nodes={nodes}
            edges={edges}
            artifacts={artifacts}
            selectedNodeId={selectedNode ? selectedNode.id : null}
            onSelectNode={(n) => setSelectedNode(n)}
          />
        )}

        {/* Live Activity Feed */}
        <LiveActivityFeed events={events} seqCounter={seqCounter} />
      </div>

      {/* Node Inspection Side Panel Drawer */}
      <NodeInspectionPanel
        node={selectedNode}
        artifacts={artifacts}
        onClose={() => setSelectedNode(null)}
      />
    </div>
  );
}
