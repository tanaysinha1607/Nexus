import asyncio
from sqlalchemy import select
from app.database import async_session
from app.models import Node, Artifact, Run

async def main():
    async with async_session() as db:
        failed_run_id = "48b19bd0-f219-4fd5-b16c-4d1025070086"
        run = (await db.execute(select(Run).where(Run.id == failed_run_id))).scalar_one()
        nodes = (await db.execute(select(Node).where(Node.run_id == failed_run_id))).scalars().all()
        arts = (await db.execute(select(Artifact).where(Artifact.run_id == failed_run_id))).scalars().all()

        print(f"=== FAILED RUN DETAILS (Run ID: {failed_run_id}) ===")
        print(f"Run Status: {run.status}")

        print("\nFAILED RUN DB NODES:")
        for n in nodes:
            print(f"  - Node ID: {n.id} | Name: {n.name} | Role: {n.agent_role} | Status: {n.status} | Logs: {n.logs}")

        raw_art = next((a for a in arts if a.kind == "raw_response"), None)
        if raw_art:
            print(f"\nRAW RESPONSE ARTIFACT LENGTH: {len(raw_art.content)} chars")

if __name__ == "__main__":
    asyncio.run(main())
