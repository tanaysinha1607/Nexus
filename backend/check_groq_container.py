import asyncio
import os
from orchestrator.llm.factory import get_default_llm_client
from orchestrator.llm.groq_client import GroqLLMClient

async def main():
    client = get_default_llm_client()
    print("INSTANTIATED CLIENT TYPE:", type(client).__name__)
    print("ACTIVE MODEL:", getattr(client, "model", "N/A"))
    if isinstance(client, GroqLLMClient):
        print("GROQ KEY FIRST 6 CHARS:", client.api_key[:6])
        res = await client.complete(
            system="System probe",
            messages=[{"role": "user", "content": "Ping"}],
            max_tokens=10,
            temperature=0.0,
        )
        print("LIVE CONTAINER COMPLETED SUCCESSFULLY!")
        print("RESPONSE:", res.text.strip())

if __name__ == "__main__":
    asyncio.run(main())
