interface LandingPageProps {
  onLaunch: () => void;
}

export function LandingPage({ onLaunch }: LandingPageProps) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8 font-sans flex flex-col justify-between selection:bg-purple-500 selection:text-white">
      <div className="max-w-6xl mx-auto w-full space-y-10 py-4">
        
        {/* Header / Brand */}
        <div className="flex items-center justify-between border-b border-gray-800/80 pb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 via-cyan-500 to-emerald-500 p-0.5 shadow-lg shadow-purple-950/50 flex items-center justify-center">
              <div className="w-full h-full bg-gray-950 rounded-[10px] flex items-center justify-center font-black text-cyan-400 text-lg">
                N
              </div>
            </div>
            <span className="text-xl font-extrabold tracking-tight bg-gradient-to-r from-purple-400 via-cyan-400 to-emerald-400 bg-clip-text text-transparent">
              Nexus
            </span>
          </div>
          <span className="px-3 py-1 rounded-full bg-purple-950/80 text-purple-300 font-mono text-xs font-semibold border border-purple-800/60 shadow-inner">
            Autonomous Software Engine
          </span>
        </div>

        {/* Hero Section */}
        <div className="text-center space-y-6 max-w-4xl mx-auto pt-2">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-gray-900 border border-gray-800 text-xs font-mono text-cyan-400 shadow-sm">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Deterministic Execution Gates • Zero LLM Self-Rating
          </div>

          <h1 className="text-4xl md:text-6xl font-black tracking-tight leading-tight">
            Autonomous software engineering, verified by{" "}
            <span className="bg-gradient-to-r from-purple-400 via-cyan-400 to-emerald-400 bg-clip-text text-transparent">
              real execution
            </span>{" "}
            — not an LLM's opinion.
          </h1>

          <p className="text-gray-300 text-base md:text-lg max-w-2xl mx-auto leading-relaxed">
            Nexus accepts a plain-English prompt and orchestrates specialized AI agents to generate, build, and verify real web applications in Python and Node.js. Nothing passes on an LLM's say-so — every step is verified by real compilers, static scanners, and sandboxed test suites.
          </p>

          <div className="pt-2">
            <button
              onClick={onLaunch}
              className="px-8 py-4 rounded-2xl bg-gradient-to-r from-purple-600 via-cyan-600 to-emerald-600 hover:from-purple-500 hover:to-emerald-500 text-white font-extrabold text-base shadow-2xl shadow-purple-950/80 hover:shadow-cyan-950/80 transition-all duration-200 transform hover:-translate-y-0.5 cursor-pointer flex items-center gap-3 mx-auto group"
            >
              <span>Launch Nexus Engine</span>
              <span className="text-lg transition-transform group-hover:translate-x-1">▶</span>
            </button>
          </div>
        </div>

        {/* Pipeline Stage Banner */}
        <div className="bg-gray-900/60 border border-gray-800/80 rounded-2xl p-4 text-center">
          <span className="text-xs font-mono text-gray-400">
            Pipeline Stages:{" "}
            <strong className="text-purple-300">
              PM → Architect → ApiDesigner → Backend → Executor → Validator → Senior Reviewer
            </strong>
          </span>
        </div>

        {/* Capabilities Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-2">
          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-purple-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-purple-950/80 border border-purple-800/60 flex items-center justify-center text-purple-400 font-bold text-sm">
              ⚡
            </div>
            <h3 className="font-bold text-gray-100 text-base">Prompt &amp; Language Generality</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              Builds full-stack web APIs in Python (FastAPI) or Node.js (Express) from plain-English specifications, declared via dynamic build manifests.
            </p>
          </div>

          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-cyan-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-cyan-950/80 border border-cyan-800/60 flex items-center justify-center text-cyan-400 font-bold text-sm">
              🛡️
            </div>
            <h3 className="font-bold text-gray-100 text-base">Six Objective Verification Gates</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              Runtime boots, black-box HTTP tests (<code className="text-cyan-300">pytest</code>/<code className="text-cyan-300">npm test</code>), <code className="text-cyan-300">tsc</code> compilation, <code className="text-cyan-300">bandit</code>/<code className="text-cyan-300">semgrep</code> AST security scans, <code className="text-cyan-300">hadolint</code>, and code review.
            </p>
          </div>

          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-emerald-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-emerald-950/80 border border-emerald-800/60 flex items-center justify-center text-emerald-400 font-bold text-sm">
              🔄
            </div>
            <h3 className="font-bold text-gray-100 text-base">Self-Healing Rework Loop</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              When a gate fails, exact empirical tracebacks are passed back to producing agents to fix vulnerabilities and bugs in attempt-scoped subchains.
            </p>
          </div>

          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-amber-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-amber-950/80 border border-amber-800/60 flex items-center justify-center text-amber-400 font-bold text-sm">
              🚀
            </div>
            <h3 className="font-bold text-gray-100 text-base">Autonomous Shipping</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              Automatically commits verified code and opens pull requests on GitHub with zero LLM tokens once every gate passes.
            </p>
          </div>

          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-pink-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-pink-950/80 border border-pink-800/60 flex items-center justify-center text-pink-400 font-bold text-sm">
              ⚙️
            </div>
            <h3 className="font-bold text-gray-100 text-base">Artifact-Gated Architecture</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              Enforces structural separation between proposal (agents), sandboxed execution (executors), and rule-based validation (validators).
            </p>
          </div>

          <div className="bg-gray-900/70 border border-gray-800/80 rounded-2xl p-6 space-y-3 hover:border-indigo-800/50 transition">
            <div className="w-8 h-8 rounded-lg bg-indigo-950/80 border border-indigo-800/60 flex items-center justify-center text-indigo-400 font-bold text-sm">
              📊
            </div>
            <h3 className="font-bold text-gray-100 text-base">Live DAG Stream &amp; Inspection</h3>
            <p className="text-gray-400 text-xs leading-relaxed">
              Inspect parallel execution nodes, attempt counts, test metrics, and exact LLM prompts in real-time over WebSocket streams.
            </p>
          </div>
        </div>

      </div>

      {/* Footer */}
      <footer className="text-center border-t border-gray-900 py-4 mt-6">
        <p className="text-xs font-mono text-gray-500">
          Nexus Autonomous Engineering Engine • Verifiable Execution &amp; Self-Healing Pipeline
        </p>
      </footer>
    </div>
  );
}
