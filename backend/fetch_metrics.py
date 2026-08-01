import asyncio
import json
import re
from sqlalchemy import select
from app.database import async_session
from app.models import Artifact, Node, Run

async def inspect_metrics():
    async with async_session() as db:
        latest_run_stmt = select(Run).order_by(Run.created_at.desc()).limit(1)
        latest_run = (await db.execute(latest_run_stmt)).scalar_one()
        run_id = str(latest_run.id)
        
        nodes_stmt = select(Node).where(Node.run_id == latest_run.id).order_by(Node.created_at)
        nodes = (await db.execute(nodes_stmt)).scalars().all()
        
        arts_stmt = select(Artifact).where(Artifact.run_id == latest_run.id)
        artifacts = (await db.execute(arts_stmt)).scalars().all()

        print(f"=== RUN METRICS REPORT (Run ID: {run_id}) ===")
        print(f"Run Status: {latest_run.status}\n")

        for node in nodes:
            print(f"NODE METRICS: Name={node.name} | Role={node.agent_role}")
            print(f"  Status: {node.status}")
            print(f"  Logs: {node.logs}")
            
            prompt_art = next((a for a in artifacts if a.node_id == node.id and a.kind == "prompt"), None)
            if prompt_art:
                prompt_len = len(prompt_art.content)
                est_in_tokens = prompt_len // 4
                role_max_tokens = 4000 if node.agent_role == "backend_engineer" else 3000
                total_req = est_in_tokens + role_max_tokens
                print(f"  Input Chars: {prompt_len} (~{est_in_tokens} input tokens)")
                print(f"  max_tokens: {role_max_tokens}")
                print(f"  Total Requested (Input + max_tokens): {total_req} tokens (TPM Headroom: {7500 - total_req})")
            print("-" * 50)

        print("\n=== PRODUCED SOURCE_CODE ARTIFACTS BY BACKEND_ENGINEER ===")
        backend_arts = [a for a in artifacts if a.produced_by_role == "backend_engineer"]
        print(f"Total source_code files emitted: {len(backend_arts)}")
        for a in backend_arts:
            print(f"\n--- FILE: {a.filename} ({len(a.content)} chars) ---")
            print(a.content)
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(inspect_metrics())
