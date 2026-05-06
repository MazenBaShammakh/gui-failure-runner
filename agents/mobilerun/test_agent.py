import asyncio
import os
from mobilerun import MobileAgent, MobileConfig

# Set whichever key matches your provider
os.environ["OPENAI_API_KEY"] = "sk-..."
# os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."
# os.environ["GEMINI_API_KEY"] = "..."


async def main():
    config = MobileConfig()   # picks up env vars automatically

    agent = MobileAgent(
        goal="Open Settings and check the Wi-Fi status",
        config=config,
    )

    result = await agent.run()

    print("success:", result.success)
    print("reason: ", result.reason)
    print("steps:  ", result.steps)


asyncio.run(main())
