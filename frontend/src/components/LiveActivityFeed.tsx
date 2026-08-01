import React from "react";
import type { NexusEvent } from "../types";

interface LiveActivityFeedProps {
  events: NexusEvent[];
  seqCounter: number;
}

export const LiveActivityFeed: React.FC<LiveActivityFeedProps> = ({
  events,
  seqCounter,
}) => {
  const getEventBadge = (evt: NexusEvent) => {
    if (evt.type === "node_status_changed") {
      const nodeName = evt.node_type || "Node";
      if (evt.new_status === "running") {
        return {
          badge: "RUNNING",
          bg: "bg-amber-500/20 text-amber-300 border-amber-500/40",
          text: `⚡ ${nodeName} status changed to RUNNING`,
        };
      }
      if (evt.new_status === "completed") {
        return {
          badge: "COMPLETED",
          bg: "bg-emerald-500/20 text-emerald-400 border-emerald-500/40",
          text: `✓ ${nodeName} completed execution`,
        };
      }
      if (evt.new_status === "failed") {
        return {
          badge: "FAILED",
          bg: "bg-rose-500/20 text-rose-400 border-rose-500/40 font-bold",
          text: `✗ ${nodeName} execution failed`,
        };
      }
      return {
        badge: (evt.new_status || "STATUS").toUpperCase(),
        bg: "bg-gray-800 text-gray-300 border-gray-700",
        text: `${nodeName} status: ${evt.old_status} → ${evt.new_status}`,
      };
    }

    if (evt.type === "artifact_created") {
      if (evt.kind === "verdict") {
        return {
          badge: "VERDICT",
          bg: "bg-purple-500/20 text-purple-300 border-purple-500/40 font-bold",
          text: `⚖️ Validation verdict artifact created (v${evt.version})`,
        };
      }
      return {
        badge: "ARTIFACT",
        bg: "bg-cyan-500/20 text-cyan-300 border-cyan-500/40",
        text: `📄 Artifact created: ${evt.kind} (${evt.filename || "file"}) v${evt.version || 1}`,
      };
    }

    if (evt.type === "run_status_changed") {
      return {
        badge: "RUN STATE",
        bg: "bg-purple-600/30 text-purple-200 border-purple-500/50 font-bold",
        text: `🚀 Run status changed: ${evt.old_status} → ${evt.new_status}`,
      };
    }

    return {
      badge: "EVENT",
      bg: "bg-gray-800 text-gray-400 border-gray-700",
      text: `Event: ${evt.type}`,
    };
  };

  return (
    <div className="bg-gray-900/70 border border-gray-800/90 rounded-2xl p-6 space-y-4 shadow-xl">
      <div className="flex items-center justify-between border-b border-gray-800/60 pb-3">
        <div>
          <h3 className="text-lg font-bold text-gray-100 flex items-center gap-2">
            <span>Live Activity Stream &amp; Rework Narrative</span>
            <span className="text-xs font-mono font-normal px-2.5 py-0.5 rounded-full bg-purple-950 text-purple-300 border border-purple-800/50">
              Redis PubSub Stream
            </span>
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Real-time chronological events broadcast over WebSocket.
          </p>
        </div>

        <div className="text-right font-mono">
          <div className="text-[10px] text-gray-500 uppercase">Sequence</div>
          <div className="text-lg font-bold text-purple-400">#{seqCounter}</div>
        </div>
      </div>

      {/* Activity Items List */}
      <div className="max-h-72 overflow-y-auto space-y-2 pr-2 font-mono text-xs">
        {events.length === 0 ? (
          <div className="text-gray-500 italic p-4 text-center">
            No events received yet. Trigger a run to watch live DAG execution.
          </div>
        ) : (
          events.map((evt, i) => {
            const meta = getEventBadge(evt);
            return (
              <div
                key={i}
                className="flex items-center justify-between bg-gray-950/90 p-3 rounded-xl border border-gray-800/80 hover:border-gray-700 transition"
              >
                <div className="flex items-center gap-3">
                  <span className="text-purple-400 font-bold text-[11px]">#{evt.seq}</span>
                  <span className={`px-2 py-0.5 rounded border text-[10px] font-bold ${meta.bg}`}>
                    {meta.badge}
                  </span>
                  <span className="text-gray-200">{meta.text}</span>
                </div>

                <div className="text-gray-500 text-[11px]">
                  {evt.ts ? new Date(evt.ts).toLocaleTimeString() : ""}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
