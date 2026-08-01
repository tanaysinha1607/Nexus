import React from "react";
import type { ArtifactData, EdgeData, NodeData } from "../types";

interface DagCanvasProps {
  nodes: NodeData[];
  edges: EdgeData[];
  artifacts: ArtifactData[];
  selectedNodeId: string | null;
  onSelectNode: (node: NodeData) => void;
}

export const DagCanvas: React.FC<DagCanvasProps> = ({
  nodes,
  artifacts,
  selectedNodeId,
  onSelectNode,
}) => {
  // Helper to find verdict for a validator node
  const getVerdictForNode = (node: NodeData) => {
    if (node.node_type !== "validator") return null;
    const verdictArt = artifacts.find(
      (a) =>
        a.kind === "verdict" &&
        (a.node_id === node.id || a.attempt === node.attempt)
    );
    if (!verdictArt) return null;
    try {
      const data = JSON.parse(verdictArt.content);
      return data.passed ? "PASS" : "FAIL";
    } catch {
      const str = verdictArt.content.trim().toUpperCase();
      return str.includes("PASS") ? "PASS" : "FAIL";
    }
  };

  // Helper to find review verdict for a senior_reviewer node
  const getReviewVerdictForNode = (node: NodeData) => {
    if (node.agent_role !== "senior_reviewer") return null;
    const reviewArt = artifacts.find(
      (a) =>
        (a.kind === "review" || a.filename === "review.md") &&
        (a.node_id === node.id || a.attempt === node.attempt)
    );
    if (!reviewArt) return null;
    const isApproved = reviewArt.content.toLowerCase().includes("review_verdict: approved");
    return isApproved ? "APPROVED" : "CHANGES_REQUESTED";
  };

  // Group nodes by attempt
  const attemptsMap = new Map<number, NodeData[]>();
  nodes.forEach((n) => {
    const att = n.attempt || 1;
    if (!attemptsMap.has(att)) {
      attemptsMap.set(att, []);
    }
    attemptsMap.get(att)!.push(n);
  });

  const sortedAttempts = Array.from(attemptsMap.keys()).sort((a, b) => a - b);

  // Separate upfront pipeline nodes (PM, Architect, ApiDesigner) vs attempt-based subchains
  const upfrontNodes = (attemptsMap.get(1) || []).filter(
    (n) => n.name === "PM" || n.name === "Architect" || n.name === "ApiDesigner"
  );

  const getNodeTypeBadge = (nodeType: string) => {
    switch (nodeType) {
      case "agent":
        return {
          cardBg: "bg-blue-950/40 border-blue-800/60 hover:border-blue-500",
          tagBg: "bg-blue-500/20 text-blue-300 border-blue-500/40",
          label: "[TYPE: AGENT]",
        };
      case "executor":
        return {
          cardBg: "bg-amber-950/40 border-amber-800/60 hover:border-amber-500",
          tagBg: "bg-amber-500/20 text-amber-300 border-amber-500/40",
          label: "[TYPE: EXECUTOR]",
        };
      case "validator":
        return {
          cardBg: "bg-purple-950/40 border-purple-800/60 hover:border-purple-500",
          tagBg: "bg-purple-500/20 text-purple-300 border-purple-500/40",
          label: "[TYPE: VALIDATOR]",
        };
      default:
        return {
          cardBg: "bg-gray-900 border-gray-800",
          tagBg: "bg-gray-800 text-gray-400",
          label: `[TYPE: ${nodeType.toUpperCase()}]`,
        };
    }
  };

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case "completed":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/40";
      case "running":
        return "bg-amber-500/20 text-amber-300 border-amber-500/50 animate-pulse shadow-md shadow-amber-950";
      case "ready":
        return "bg-cyan-500/20 text-cyan-300 border-cyan-500/40";
      case "failed":
        return "bg-rose-500/20 text-rose-400 border-rose-500/40 font-bold";
      case "cancelled":
      case "blocked":
        return "bg-purple-500/20 text-purple-400 border-purple-500/40";
      default:
        return "bg-gray-800 text-gray-400 border-gray-700";
    }
  };

  return (
    <div className="space-y-6">
      {/* 1. Primary Design Pipeline Branch */}
      {upfrontNodes.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs font-mono font-bold text-cyan-400 tracking-wider uppercase">
            <span>Primary Pipeline Phase</span>
            <span className="text-gray-600">&mdash;</span>
            <span className="text-gray-400 font-normal">Spec, Arch, Contract</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {upfrontNodes.map((n) => renderNodeCard(n))}
          </div>
        </div>
      )}

      {/* 2. Rework Execution Subchains per Attempt */}
      <div className="space-y-6">
        {sortedAttempts.map((att) => {
          const attNodes = attemptsMap.get(att) || [];
          const backendNode = attNodes.find(
            (n) => n.name.startsWith("Backend") && !n.name.includes("Executor")
          );
          const executorNode = attNodes.find(
            (n) => n.name.includes("BackendExecutor") || n.node_type === "executor"
          );
          const validatorNode = attNodes.find(
            (n) => n.name.includes("Validator") || n.node_type === "validator"
          );
          const reviewerNode = attNodes.find(
            (n) => n.name.includes("Reviewer") || n.agent_role === "senior_reviewer"
          );

          if (!backendNode && !executorNode && !validatorNode && !reviewerNode) return null;

          return (
            <div key={att} className="space-y-3 border-t border-gray-800/80 pt-5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 font-mono text-xs font-bold">
                  {att === 1 ? (
                    <span className="px-2.5 py-1 rounded bg-blue-950 text-blue-300 border border-blue-800">
                      Attempt 1: Execution &amp; Quality Review
                    </span>
                  ) : (
                    <span className="px-2.5 py-1 rounded bg-amber-950 text-amber-300 border border-amber-800 flex items-center gap-1.5">
                      <span>⚡</span>
                      <span>Attempt {att}: Self-Healing Rework Sub-chain</span>
                    </span>
                  )}
                </div>

                <span className="text-[11px] font-mono text-gray-500">
                  {attNodes.length} node(s) in attempt branch
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                {backendNode && renderNodeCard(backendNode)}
                {executorNode && renderNodeCard(executorNode)}
                {validatorNode && renderNodeCard(validatorNode)}
                {reviewerNode && renderNodeCard(reviewerNode)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );

  function renderNodeCard(node: NodeData) {
    const typeMeta = getNodeTypeBadge(node.node_type);
    const isSelected = selectedNodeId === node.id;
    const verdict = getVerdictForNode(node);
    const reviewVerdict = getReviewVerdictForNode(node);

    // Find produced artifacts count
    const nodeArts = artifacts.filter(
      (a) =>
        a.node_id === node.id ||
        (a.produced_by_role === node.agent_role && a.attempt === node.attempt)
    );

    return (
      <div
        key={node.id}
        onClick={() => onSelectNode(node)}
        className={`border rounded-xl p-4 space-y-3 cursor-pointer transition-all duration-200 relative group ${
          typeMeta.cardBg
        } ${
          isSelected
            ? "ring-2 ring-cyan-400 border-cyan-400 shadow-xl shadow-cyan-950/40 scale-[1.01]"
            : "hover:scale-[1.005]"
        }`}
      >
        {/* Attempt Badge on Top Right */}
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="font-bold text-sm text-gray-100 flex items-center gap-2 group-hover:text-cyan-300 transition">
              <span>{node.name}</span>
              {node.attempt > 1 && (
                <span className="text-[10px] font-mono font-extrabold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">
                  Attempt {node.attempt}/5
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <span className={`px-2 py-0.5 rounded border text-[10px] font-mono font-bold tracking-wider ${typeMeta.tagBg}`}>
                {typeMeta.label}
              </span>
              {node.agent_role && (
                <span className="text-[10px] font-mono text-gray-400">({node.agent_role})</span>
              )}
            </div>
          </div>

          <span className={`px-2.5 py-1 rounded-md border text-[11px] font-mono font-bold uppercase tracking-wider ${getStatusBadgeClass(node.status)}`}>
            {node.status}
          </span>
        </div>

        {/* Validator Verdict Visibility Section */}
        {node.node_type === "validator" && (
          <div className="border-t border-purple-800/40 pt-2.5 space-y-1.5">
            <div className="text-[10px] font-mono uppercase font-semibold text-purple-300 flex items-center justify-between">
              <span>Validation Verdict</span>
              <span className="text-[9px] text-purple-400">Rule-based Check</span>
            </div>

            <div className="flex items-center justify-between bg-purple-950/60 px-3 py-1.5 rounded-lg border border-purple-800/60">
              <span className="text-xs text-gray-300 font-mono">Verdict Result:</span>
              {verdict === "PASS" ? (
                <span className="px-3 py-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/50 font-mono text-xs font-black uppercase tracking-wider shadow-sm shadow-emerald-950">
                  ✓ PASS
                </span>
              ) : verdict === "FAIL" ? (
                <span className="px-3 py-1 rounded bg-rose-600 text-white border border-rose-500 font-mono text-xs font-black uppercase tracking-wider shadow-lg shadow-rose-950">
                  ✗ FAIL
                </span>
              ) : (
                <span className="text-xs text-gray-500 italic">Evaluating...</span>
              )}
            </div>

            {node.status === "completed" && verdict === "FAIL" && (
              <div className="text-[10px] font-mono text-rose-300 bg-rose-950/60 p-1.5 rounded border border-rose-800/70 flex items-center gap-1.5">
                <span>⚠️</span>
                <span>Node 'completed' (check ran), Verdict: FAIL</span>
              </div>
            )}
          </div>
        )}

        {/* Reviewer Verdict Section */}
        {node.agent_role === "senior_reviewer" && (
          <div className="border-t border-blue-800/40 pt-2.5 space-y-1.5">
            <div className="text-[10px] font-mono uppercase font-semibold text-blue-300 flex items-center justify-between">
              <span>Reviewer Gate</span>
              <span className="text-[9px] text-blue-400">Subjective Quality Check</span>
            </div>

            <div className="flex items-center justify-between bg-blue-950/60 px-3 py-1.5 rounded-lg border border-blue-800/60">
              <span className="text-xs text-gray-300 font-mono">Review Verdict:</span>
              {reviewVerdict === "APPROVED" ? (
                <span className="px-3 py-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/50 font-mono text-xs font-black uppercase tracking-wider shadow-sm shadow-emerald-950">
                  ✓ APPROVED
                </span>
              ) : reviewVerdict === "CHANGES_REQUESTED" ? (
                <span className="px-3 py-1 rounded bg-amber-500/20 text-amber-300 border border-amber-500/50 font-mono text-xs font-black uppercase tracking-wider shadow-sm shadow-emerald-950">
                  ⚡ CHANGES REQUESTED
                </span>
              ) : (
                <span className="text-xs text-gray-500 italic">Reviewing...</span>
              )}
            </div>
          </div>
        )}

        {/* Produced Artifacts Preview */}
        <div className="border-t border-gray-800/80 pt-2 flex items-center justify-between text-xs font-mono text-gray-400">
          <span>Artifacts: {nodeArts.length} file(s)</span>
          <span className="text-cyan-400 group-hover:underline text-[11px] font-semibold">Inspect Node &rarr;</span>
        </div>
      </div>
    );
  }
};
