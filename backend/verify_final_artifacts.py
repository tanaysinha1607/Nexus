import asyncio
import json
import re
from sqlalchemy import select
from app.database import async_session
from app.models import Artifact, Run

FORBIDDEN_TERMS = ["Next.js", "NestJS", "Node", "Express", "Prisma", "Go", "Golang", "MongoDB"]

async def verify_artifacts():
    async with async_session() as db:
        latest_run_stmt = select(Run).order_by(Run.created_at.desc()).limit(1)
        latest_run = (await db.execute(latest_run_stmt)).scalar_one()
        run_id = str(latest_run.id)
        
        arts_stmt = select(Artifact).where(Artifact.run_id == latest_run.id)
        artifacts = (await db.execute(arts_stmt)).scalars().all()

        arch_art = next((a for a in artifacts if a.kind == "architecture"), None)
        contract_art = next((a for a in artifacts if a.kind == "api_contract"), None)

        print(f"=== VERIFICATION OF RUN {run_id} ARTIFACTS ===")
        
        # 1. Verify JSON parse & 3 core endpoints
        assert contract_art is not None, "api_contract artifact missing!"
        contract_data = json.loads(contract_art.content)
        endpoints = contract_data.get("endpoints", [])
        print(f"[OK] api_contract.json parsed with json.loads() cleanly! Total endpoints: {len(endpoints)}")
        assert len(endpoints) >= 3, f"Expected >= 3 endpoints, found {len(endpoints)}"

        core_paths = ["/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/portfolio/summary"]
        for path in core_paths:
            ep = next((e for e in endpoints if e.get("path") == path), None)
            assert ep is not None, f"Core endpoint {path} missing in api_contract!"
            assert "request_schema" in ep and "response_schema" in ep, f"Missing schemas for {path}"
            print(f"  - Core Endpoint: {ep['method']} {ep['path']} -> request_schema keys: {list(ep['request_schema'].keys())}, response_schema keys: {list(ep['response_schema'].keys())}")

        # 2. Grep forbidden stack terms
        print("\n=== FORBIDDEN TECH STACK SEARCH ===")
        arch_hits = [term for term in FORBIDDEN_TERMS if re.search(r"\b" + re.escape(term) + r"\b", arch_art.content, re.IGNORECASE)]
        contract_hits = [term for term in FORBIDDEN_TERMS if re.search(r"\b" + re.escape(term) + r"\b", contract_art.content, re.IGNORECASE)]

        print(f"Forbidden terms in architecture.md: {arch_hits if arch_hits else 'NONE (0 hits)'}")
        print(f"Forbidden terms in api_contract.json: {contract_hits if contract_hits else 'NONE (0 hits)'}")

        # Save files locally for verbatim output
        with open("verbatim_architecture.md", "w", encoding="utf-8") as f:
            f.write(arch_art.content)
            
        with open("verbatim_api_contract.json", "w", encoding="utf-8") as f:
            f.write(contract_art.content)

if __name__ == "__main__":
    asyncio.run(verify_artifacts())
