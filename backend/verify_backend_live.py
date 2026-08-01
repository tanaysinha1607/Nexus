import asyncio
from sqlalchemy import select
from app.database import async_session
from app.models import Artifact, Node, Run

async def verify():
    async with async_session() as db:
        latest_run_stmt = select(Run).order_by(Run.created_at.desc()).limit(1)
        latest_run = (await db.execute(latest_run_stmt)).scalar_one()
        run_id = str(latest_run.id)

        nodes_stmt = select(Node).where(Node.run_id == latest_run.id).order_by(Node.created_at)
        nodes = (await db.execute(nodes_stmt)).scalars().all()

        arts_stmt = select(Artifact).where(Artifact.run_id == latest_run.id)
        artifacts = (await db.execute(arts_stmt)).scalars().all()

        print(f"=== LIVE 4-NODE CHAIN VERIFICATION REPORT (Run ID: {run_id}) ===")
        print(f"Run Status: {latest_run.status}\n")

        for node in nodes:
            prompt_art = next((a for a in artifacts if a.node_id == node.id and a.kind == "prompt"), None)
            if prompt_art:
                prompt_len = len(prompt_art.content)
                est_in_tokens = prompt_len // 4
                role_max_tokens = 4000 if node.agent_role == "backend_engineer" else 3000
                total_req = est_in_tokens + role_max_tokens
                print(f"Node: {node.name} ({node.agent_role})")
                print(f"  Status: {node.status}")
                print(f"  Input Chars: {prompt_len} (~{est_in_tokens} tokens)")
                print(f"  max_tokens: {role_max_tokens}")
                print(f"  Total Requested (Input + max_tokens): {total_req} tokens (TPM Headroom: {7500 - total_req})")
                print("-" * 50)

        backend_arts = [a for a in artifacts if a.produced_by_role == "backend_engineer" and a.kind == "source_code"]
        print(f"\nBackend Emitted Source Code Files Count: {len(backend_arts)}")

        main_art = next((a for a in backend_arts if a.filename == "main.py"), None)
        assert main_art is not None, "main.py missing!"

        # Confirm main.py compile()s
        compile(main_art.content, "main.py", "exec")
        print("[OK] main.py compiled with stdlib compile() cleanly!")

        # Confirm GET /health is in main.py
        assert "/health" in main_art.content, "GET /health missing in main.py!"
        print("[OK] GET /health endpoint verified in main.py!")

        # Confirm NO forbidden external deps
        forbidden = ["sqlalchemy", "alembic", "psycopg", "asyncpg", "redis"]
        for f in backend_arts:
            for dep in forbidden:
                assert dep not in f.content.lower(), f"Forbidden dependency {dep} found in {f.filename}!"
        print("[OK] Zero forbidden external dependencies found across all backend source code artifacts!")

        # Save files for verbatim output
        for a in backend_arts:
            with open(f"verbatim_{a.filename}", "w", encoding="utf-8") as out_f:
                out_f.write(a.content)

if __name__ == "__main__":
    asyncio.run(verify())
