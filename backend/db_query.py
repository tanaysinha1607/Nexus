import asyncio
from sqlalchemy import select
from app.database import async_session
from app.models import Node, Artifact, Run, Project

async def main():
    async with async_session() as db:
        all_projects = (await db.execute(select(Project))).scalars().all()
        all_runs = (await db.execute(select(Run))).scalars().all()
        all_nodes = (await db.execute(select(Node))).scalars().all()
        
        print("=== DATABASE OVERALL STATS ===")
        print(f"Total Projects in DB: {len(all_projects)}")
        print(f"Total Runs in DB: {len(all_runs)}")
        print(f"Total Nodes Executed in DB: {len(all_nodes)}")

        pm_calls = sum(1 for n in all_nodes if n.agent_role == "product_manager" and n.status in ("completed", "failed"))
        arch_calls = sum(1 for n in all_nodes if n.agent_role == "solution_architect" and n.status in ("completed", "failed"))
        total_gemini_calls = pm_calls + arch_calls

        print(f"Total PM agent calls (Gemini): {pm_calls}")
        print(f"Total Architect agent calls (Gemini): {arch_calls}")
        print(f"Total Gemini LLM calls across all test loops: {total_gemini_calls}")

        latest_run_stmt = select(Run).order_by(Run.created_at.desc()).limit(1)
        latest_run = (await db.execute(latest_run_stmt)).scalar_one()
        run_id = str(latest_run.id)
        print(f"\n=== TARGET SINGLE RUN DETAILS (Run ID: {run_id}) ===")
        print(f"Run Status: {latest_run.status}")

        nodes = (await db.execute(select(Node).where(Node.run_id == run_id))).scalars().all()
        arts = (await db.execute(select(Artifact).where(Artifact.run_id == run_id))).scalars().all()

        print("\nNODE ROWS VERBATIM FROM DB:")
        for n in nodes:
            print(f"  - Node ID: {n.id} | Name: {n.name} | Role: {n.agent_role} | Status: {n.status} | Logs: {n.logs}")

        print("\nARTIFACTS IN LATEST RUN:")
        for a in arts:
            print(f"  - Artifact ID: {a.id} | Kind: {a.kind} | Filename: {a.filename} | Role: {a.produced_by_role} | NodeID: {a.node_id}")

        for a in arts:
            if a.kind == "prompt":
                print(f"\n--- PROMPT ARTIFACT ({a.filename}) ---")
                print(f"Length: {len(a.content)} chars")

if __name__ == "__main__":
    asyncio.run(main())
