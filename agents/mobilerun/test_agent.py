import asyncio
import os
from pathlib import Path
from mobilerun import MobileAgent, MobileConfig

# Load .env from repo root if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
except ImportError:
    pass  # set env vars manually or export them in your shell


async def main():
    config = MobileConfig()   # picks up OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY

    agent = MobileAgent(
        goal="Open Settings and check the Wi-Fi status",
        config=config,
    )

    result = await agent.run()

    print("success:", result.success)
    print("reason: ", result.reason)
    print("steps:  ", result.steps)


asyncio.run(main())
