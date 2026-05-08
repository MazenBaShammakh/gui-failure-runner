import asyncio
from pathlib import Path
from seeact.agent import SeeActAgent

# Load .env from repo root if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
except ImportError:
    pass  # set env vars manually or export them in your shell


async def main():
    agent = SeeActAgent(
        model="gpt-4o",
        default_task="Go to google.com and search for 'weather in Munich'",
        default_website="https://www.google.com/",
        save_file_dir="seeact_test_output",
    )
    await agent.start()
    steps = 0
    while not agent.complete_flag and steps < 5:
        pred = await agent.predict()
        await agent.execute(pred)
        steps += 1
    await agent.stop()
    print("complete_flag:", agent.complete_flag)
    print("steps:", steps)


asyncio.run(main())
