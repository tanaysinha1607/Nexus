import React, { useState } from "react";
import type { ArtifactData, NodeData } from "../types";

interface NodeInspectionPanelProps {
  node: NodeData | null;
  artifacts: ArtifactData[];
  onClose: () => void;
}

export const NodeInspectionPanel: React.FC<NodeInspectionPanelProps> = ({
  node,
  artifacts,
  onClose,
}) => {
  const [activeCodeTab, setActiveCodeTab] = useState<string>("main.py");
  const [showPromptModal, setShowPromptModal] = useState<boolean>(false);

  if (!node) return null;

  // Find output artifacts produced by this node or belonging to its attempt & role
  const nodeArtifacts = artifacts.filter(
    (a) =>
      a.node_id === node.id ||
      (a.produced_by_role === node.agent_role && a.attempt === node.attempt)
  );

  // Find prompt artifact if this is an agent node
  const promptArtifact = artifacts.find(
    (a) => a.kind === "prompt" && (a.node_id === node.id || a.attempt === node.attempt)
  );

  // Source code files
  const sourceCodeArts = nodeArtifacts.filter((a) => a.kind === "source_code");

  // Execution report
  const execReportArt = nodeArtifacts.find((a) => a.kind === "execution_report");
  const testReportArt = nodeArtifacts.find((a) => a.kind === "test_report");

  // Verdict
  const verdictArt = nodeArtifacts.find((a) => a.kind === "verdict");

  // Format JSON content safely
  const formatJson = (content: string) => {
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      return content;
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 w-full max-w-2xl bg-gray-950/95 backdrop-blur-xl border-l border-gray-800 shadow-2xl z-50 flex flex-col justify-between overflow-hidden text-gray-100">
      {/* Header */}
      <div className="p-6 border-b border-gray-800/80 bg-gray-900/80 flex items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h3 className="text-xl font-bold text-gray-100">{node.name}</h3>
            <span className="px-2.5 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800/60 font-mono text-xs font-bold">
              Attempt {node.attempt} of 5
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs font-mono text-gray-400">
            <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 font-bold uppercase">
              {node.node_type}
            </span>
            {node.agent_role && <span className="text-cyan-400">role: {node.agent_role}</span>}
            <span className="text-gray-600">|</span>
            <span>ID: {node.id.slice(0, 8)}...</span>
          </div>

          {node.rework_of_id && (
            <div className="text-xs font-mono text-amber-400 bg-amber-950/40 px-2 py-1 rounded border border-amber-800/50 mt-1 inline-block">
              🔄 Rework of Node: {node.rework_of_id.slice(0, 8)}...
            </div>
          )}
        </div>

        <button
          onClick={onClose}
          className="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-100 transition cursor-pointer text-sm font-bold font-mono"
        >
          ✕ CLOSE
        </button>
      </div>

      {/* Content Body */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Node Status & Prompt Trigger Bar */}
        <div className="bg-gray-900/60 border border-gray-800 rounded-xl p-4 flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-mono text-gray-400 uppercase font-semibold">Node Status</div>
            <div className="text-sm font-mono font-bold text-emerald-400 mt-0.5 uppercase">
              {node.status}
            </div>
          </div>

          {node.node_type === "agent" && (
            <button
              onClick={() => setShowPromptModal(true)}
              className="px-4 py-2 rounded-xl bg-purple-600/30 hover:bg-purple-600/50 border border-purple-500/40 text-purple-200 text-xs font-mono font-bold transition flex items-center gap-2 cursor-pointer shadow-sm"
            >
              <span>📋 View LLM Prompt Artifact</span>
            </button>
          )}
        </div>

        {/* 1. VERDICT ARTIFACT (If Validator) */}
        {verdictArt && renderVerdictSection(verdictArt)}

        {/* 2. EXECUTION REPORT ARTIFACT (If Executor) */}
        {execReportArt && renderExecutionReportSection(execReportArt)}
        {testReportArt && renderTestReportSection(testReportArt)}

        {/* 3. SOURCE CODE ARTIFACTS (If Backend Engineer) */}
        {sourceCodeArts.length > 0 && renderSourceCodeSection(sourceCodeArts)}

        {/* 4. OTHER ARTIFACTS (api_contract, prd, architecture, etc.) */}
        {nodeArtifacts
          .filter(
            (a) =>
              a.kind !== "source_code" &&
              a.kind !== "execution_report" &&
              a.kind !== "test_report" &&
              a.kind !== "verdict" &&
              a.kind !== "prompt"
          )
          .map((art) => renderGeneralArtifact(art))}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-gray-800 bg-gray-900/60 text-xs font-mono text-gray-500 flex justify-between">
        <span>Created: {new Date(node.created_at).toLocaleTimeString()}</span>
        <span>Attempt #{node.attempt}</span>
      </div>

      {/* Prompt Modal Overlay */}
      {showPromptModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md p-6 flex items-center justify-center">
          <div className="bg-gray-900 border border-gray-700 rounded-2xl max-w-3xl w-full max-h-[85vh] flex flex-col shadow-2xl">
            <div className="p-5 border-b border-gray-800 flex items-center justify-between">
              <h4 className="text-lg font-bold text-purple-300 font-mono">
                Exact LLM Prompt Artifact (attempt {node.attempt})
              </h4>
              <button
                onClick={() => setShowPromptModal(false)}
                className="text-gray-400 hover:text-white font-mono text-sm"
              >
                ✕ Close
              </button>
            </div>
            <div className="p-5 overflow-y-auto flex-1 font-mono text-xs text-gray-300 bg-gray-950 rounded-b-2xl whitespace-pre-wrap leading-relaxed border-t border-gray-800">
              {promptArtifact ? promptArtifact.content : "Prompt artifact content loading or stored in system prompt specs."}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  function renderVerdictSection(art: ArtifactData) {
    let passed = false;
    let failures: string[] = [];
    try {
      const data = JSON.parse(art.content);
      passed = Boolean(data.passed);
      failures = data.failures || [];
    } catch {
      passed = art.content.toLowerCase().includes("pass");
    }

    return (
      <div className="bg-gray-900/80 border border-purple-800/60 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between border-b border-purple-800/40 pb-3">
          <h4 className="text-sm font-mono font-bold uppercase text-purple-300 tracking-wider">
            Validation Verdict (Deterministic Check)
          </h4>
          <span
            className={`px-4 py-1.5 rounded-lg font-mono font-black text-sm tracking-wider uppercase ${
              passed
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/50 shadow-md shadow-emerald-950"
                : "bg-rose-600 text-white border border-rose-500 shadow-xl shadow-rose-950 animate-pulse"
            }`}
          >
            {passed ? "✓ PASS" : "✗ FAIL"}
          </span>
        </div>

        {node?.status === "completed" && !passed && (
          <div className="bg-rose-950/60 border border-rose-800/80 text-rose-200 text-xs font-mono p-3 rounded-lg flex items-start gap-2">
            <span className="text-base">⚠️</span>
            <div>
              <strong>SEPARATE STATUS &amp; VERDICT:</strong> Node execution status is 'completed' (validator executed cleanly), but the verdict output is <strong>FAIL</strong>!
            </div>
          </div>
        )}

        {failures.length > 0 && (
          <div className="space-y-1.5 font-mono text-xs">
            <div className="text-rose-400 font-bold uppercase">Failures Identified:</div>
            <ul className="list-disc list-inside space-y-1 bg-gray-950 p-3 rounded-lg border border-rose-900/50 text-rose-300">
              {failures.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  function renderExecutionReportSection(art: ArtifactData) {
    let report: any = {};
    try {
      report = JSON.parse(art.content);
    } catch {
      report = {};
    }

    const buildSuccess = report.build_success;
    const healthOk = report.health_ok;
    const logsTail = report.container_logs_tail || "";

    return (
      <div className="bg-gray-900/80 border border-amber-800/60 rounded-xl p-5 space-y-4">
        <h4 className="text-sm font-mono font-bold uppercase text-amber-400 tracking-wider flex items-center gap-2">
          <span>Execution Report &amp; Docker Metrics</span>
          <span className="text-[10px] text-amber-500/80 font-normal">Real Sandbox Run</span>
        </h4>

        {/* Prominent Proof-of-Realness Status Cards */}
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-gray-950 p-3 rounded-lg border border-gray-800 flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">Docker Image Build</span>
            <span
              className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold ${
                buildSuccess
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : "bg-rose-500/20 text-rose-400 border border-rose-500/40"
              }`}
            >
              {buildSuccess ? "SUCCESS" : "FAILED"}
            </span>
          </div>

          <div className="bg-gray-950 p-3 rounded-lg border border-gray-800 flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">/health Probe (HTTP 200)</span>
            <span
              className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold ${
                healthOk
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : "bg-rose-600 text-white border border-rose-500 shadow-md"
              }`}
            >
              {healthOk ? "OK (200)" : "FAILED"}
            </span>
          </div>
        </div>

        {/* PROMINENT CONTAINER LOGS & TRACEBACK TERMINAL */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-cyan-300 font-bold uppercase flex items-center gap-1.5">
              <span>🖥️ Container Logs &amp; Python Traceback</span>
              <span className="text-[10px] text-gray-500">(Proof-of-Realness)</span>
            </span>
            <span className="text-gray-500">stdout + stderr</span>
          </div>

          <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 font-mono text-xs text-gray-300 max-h-72 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {logsTail ? (
              <span className={logsTail.includes("Traceback") || logsTail.includes("Error") ? "text-rose-300 font-medium" : "text-emerald-300"}>
                {logsTail}
              </span>
            ) : (
              <span className="text-gray-600 italic">No container logs captured.</span>
            )}
          </div>
        </div>

        {/* Raw JSON Details */}
        <details className="text-xs font-mono text-gray-400">
          <summary className="cursor-pointer hover:text-gray-200 py-1">View Raw execution_report.json</summary>
          <pre className="bg-gray-950 p-3 rounded-lg border border-gray-800 text-[11px] overflow-x-auto text-cyan-300 mt-2">
            {formatJson(art.content)}
          </pre>
        </details>
      </div>
    );
  }

  function renderTestReportSection(art: ArtifactData) {
    let report: any = {};
    try {
      report = JSON.parse(art.content);
    } catch {
      report = {};
    }

    const serviceBooted = report.service_booted;
    const passed = report.passed || 0;
    const failed = report.failed || 0;
    const pytestTail = report.pytest_output_tail || "";

    return (
      <div className="bg-gray-900/80 border border-emerald-800/60 rounded-xl p-5 space-y-4">
        <h4 className="text-sm font-mono font-bold uppercase text-emerald-400 tracking-wider flex items-center gap-2">
          <span>🧪 Contract Test Execution Report</span>
          <span className="text-[10px] text-emerald-500/80 font-normal">Black-Box Integration Tests</span>
        </h4>

        <div className="grid grid-cols-3 gap-3">
          <div className="bg-gray-950 p-3 rounded-lg border border-gray-800 flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">Service Boot</span>
            <span
              className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold ${
                serviceBooted
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  : "bg-rose-500/20 text-rose-400 border border-rose-500/40"
              }`}
            >
              {serviceBooted ? "BOOTED (200)" : "FAILED"}
            </span>
          </div>

          <div className="bg-gray-950 p-3 rounded-lg border border-gray-800 flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">Tests Passed</span>
            <span className="px-2.5 py-0.5 rounded text-xs font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
              {passed} PASSED
            </span>
          </div>

          <div className="bg-gray-950 p-3 rounded-lg border border-gray-800 flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">Tests Failed</span>
            <span
              className={`px-2.5 py-0.5 rounded text-xs font-mono font-bold ${
                failed > 0
                  ? "bg-rose-500/20 text-rose-400 border border-rose-500/40"
                  : "bg-gray-800 text-gray-400"
              }`}
            >
              {failed} FAILED
            </span>
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-cyan-300 font-bold uppercase flex items-center gap-1.5">
              <span>🧪 Pytest Execution Output</span>
            </span>
          </div>

          <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 font-mono text-xs text-gray-300 max-h-72 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {pytestTail ? (
              <span className={failed > 0 ? "text-rose-300 font-medium" : "text-emerald-300"}>
                {pytestTail}
              </span>
            ) : (
              <span className="text-gray-600 italic">No pytest output captured.</span>
            )}
          </div>
        </div>
      </div>
    );
  }

  function renderSourceCodeSection(arts: ArtifactData[]) {
    const filenames = arts.map((a) => a.filename);
    const activeArt = arts.find((a) => a.filename === activeCodeTab) || arts[0];

    return (
      <div className="bg-gray-900/80 border border-blue-800/60 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-mono font-bold uppercase text-blue-300 tracking-wider">
            Generated Source Code Artifacts
          </h4>
          <span className="text-xs font-mono text-gray-400">{arts.length} file(s)</span>
        </div>

        {/* File Tabs */}
        <div className="flex items-center gap-2 border-b border-gray-800 pb-2">
          {filenames.map((name) => (
            <button
              key={name}
              onClick={() => setActiveCodeTab(name)}
              className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition cursor-pointer ${
                activeCodeTab === name
                  ? "bg-blue-600 text-white shadow-md shadow-blue-950"
                  : "bg-gray-950 text-gray-400 hover:text-gray-200 border border-gray-800"
              }`}
            >
              📄 {name}
            </button>
          ))}
        </div>

        {/* Code Content Box */}
        {activeArt && (
          <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 font-mono text-xs text-emerald-300 max-h-96 overflow-y-auto whitespace-pre leading-relaxed overflow-x-auto">
            {activeArt.content}
          </div>
        )}
      </div>
    );
  }

  function renderGeneralArtifact(art: ArtifactData) {
    const isJson = art.filename.endsWith(".json") || art.kind === "api_contract";

    return (
      <div key={art.id} className="bg-gray-900/60 border border-gray-800 rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono font-bold uppercase text-cyan-300 tracking-wider">
            Artifact: {art.kind} ({art.filename})
          </span>
          <span className="text-xs font-mono text-gray-500">v{art.version}</span>
        </div>

        <div className="bg-gray-950 p-4 rounded-xl border border-gray-800 font-mono text-xs text-gray-300 max-h-80 overflow-y-auto whitespace-pre-wrap leading-relaxed">
          {isJson ? formatJson(art.content) : art.content}
        </div>
      </div>
    );
  }
};
