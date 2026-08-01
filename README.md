# Nexus

**An orchestration engine for autonomous software engineering.**

Given a single plain-English prompt, a team of specialized AI agents collaborates on a dependency graph of tasks to build real, running software — but **nothing is ever marked "passed" or "approved" on an LLM's say-so.** Every claim of correctness is backed by real execution.

---

## The core principle

> **LLMs propose work. They never decide objective truth.**

This is enforced structurally, through three kinds of node:

| Node type | What it does | LLM involved? |
|-----------|-------------|---------------|
| **Agent** | Calls an LLM, produces artifacts (PRDs, architecture, code, reviews) — subjective output | Yes |
| **Executor** | Runs something real (a Docker build, a container, a test) and captures the actual result | No |
| **Validator** | Applies a deterministic rule to an executor's output → pass/fail verdict | No |

An agent's opinion that code "looks good" can never override a validator's objective failure. Nothing is "validated" unless a real check produced that result.

## Why it's different

Nodes communicate **only through artifacts**, never chat messages. A node declares the artifacts it needs and produces new ones. The scheduler cares only whether a node's required artifacts *exist yet* — not which agent, model, or tool produced them.

The consequence: **swap the LLM provider and the orchestration layer doesn't change.** Nexus runs on Anthropic, Gemini, or Groq behind the same interface.

## The two gates

Nexus checks generated code with two independent gates, and neither can override the other:

- **Objective gate** — a real Docker container builds and boots the code; a deterministic validator confirms it responds. Catches *"doesn't run"*: dependency conflicts, missing packages, import-time crashes.
- **Subjective gate** — a Senior Reviewer agent inspects working code for security, quality, and contract adherence. Catches *"runs but shouldn't ship"*: weak password hashing, hardcoded secrets, missing endpoints, schema drift.

A reviewer can reject working code on quality grounds, but it can **never** turn an objective failure into a pass. Objective truth first; subjective judgment second.

## Self-healing

When either gate rejects, a rework loop feeds the **real failure** — a container traceback, or the reviewer's comments — back to the agent, which makes a targeted fix and re-runs. Capped at 5 attempts.

In practice, agents have fixed:
- an `email-validator` version conflict (`>=2.0` required by Pydantic v2)
- a missing `passlib` dependency
- a Pydantic v1→v2 API change (`const` → `Literal`)
- weak `uuid5` password hashing → bcrypt
- Pydantic models missing `extra = "forbid"` for contract strictness

**None of these were catchable by static analysis** — only by running the code and reading the real error.

## The reference pipeline

```
PM ──▶ Architect ──▶ ApiDesigner ──▶ Backend ──▶ Executor ──▶ Validator ──▶ Reviewer
▲                                                                                │
└────────────────────────── rework (≤5 attempts) ────────────────────────────────┘
```

From the canonical prompt *"Build a cryptocurrency paper trading platform..."*, the agents produce a PRD, an architecture spec, a machine-validated JSON API contract, and a working FastAPI service — which is then built, booted, validated, and reviewed for real.

## Stack

FastAPI · SQLAlchemy (async) · Alembic · PostgreSQL 16 · Redis 7 · React · Vite · TypeScript · Tailwind · Docker · provider-agnostic LLM layer (Anthropic / Gemini / Groq) — all in a docker-compose monorepo.

## Running it

```bash
cp .env.example .env        # add your LLM provider API key
docker compose up --build
# frontend: http://localhost:5173
# trigger a run, watch the DAG self-heal live
```

## Build status

Built in strict phases — each fully working and demoable before the next began.

- **Phase 0** ✅ — task-graph schema, scheduler, artifact-based readiness, Redis event bus, live WebSocket UI (no LLMs — orchestration proven on fake handlers)
- **Phase 1** ✅ — the real MVP: five agents, real Docker execution, deterministic validation, self-healing rework on objective failure, and a Senior Reviewer subjective gate
- **Phase 2** ✅ — QA Engineer + Frontend Engineer (contract-based test execution & TypeScript build validation)
    - **2a** ✅ QA Engineer + real contract-based test execution (agent-written pytest runs live against generated code over HTTP)
    - **2b** ✅ Frontend Engineer + real TypeScript build validation (agent-written typed API client compiled in-sandbox via `tsc --noEmit --strict`)
- **Phase 3** 🟡 — Security ✅ (real `bandit` AST scanner, deterministic zero-HIGH gate, self-healing on AST vulnerabilities) | Performance ⬜ deferred by design (see note)
- **Phase 4** ⬜ — Documentation + DevOps agents
- **Phase 5** ⬜ — GitHub integration, multi-project memory

> **Note on Performance Validation Deferral**: Performance (latency/throughput) validation is intentionally deferred by design. Benchmarking HTTP response times on a shared Docker host is inherently non-deterministic and subject to host CPU/memory load variance. Imposing a rigid latency gate on a shared host would introduce non-deterministic validator failures, directly violating Nexus's core principle that all gate verdicts must be 100% deterministic objective truth.

## Tests

```bash
docker compose exec backend pytest tests/ -m "not live"   # 89 passing, no network
```
