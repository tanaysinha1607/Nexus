import asyncio
from sqlalchemy import select
from app.database import async_session
from app.models import Artifact, Run
from orchestrator.agents.roles import PRODUCT_MANAGER_SYSTEM_PROMPT

async def inspect_pm_prompt():
    async with async_session() as db:
        run_id = "ef2a757b-81c3-492f-9292-89338324e8bf"
        res = await db.execute(select(Artifact).where(Artifact.run_id == run_id))
        arts = res.scalars().all()
        print(f"Total Artifacts in run {run_id}: {len(arts)}")
        for a in arts:
            print(f"- Kind: {a.kind} | Filename: {a.filename} | Length: {len(a.content)} chars")
            if a.kind == "prompt":
                print("\n=== PROMPT CONTENT ===")
                print(a.content)
                print("======================\n")
            if a.kind == "user_prompt":
                print("\n=== USER PROMPT CONTENT ===")
                print(a.content)
                print("===========================\n")

        print("=== SYSTEM PROMPT DETAILS ===")
        print("System prompt chars:", len(PRODUCT_MANAGER_SYSTEM_PROMPT))
        print(PRODUCT_MANAGER_SYSTEM_PROMPT)

if __name__ == "__main__":
    asyncio.run(inspect_pm_prompt())
