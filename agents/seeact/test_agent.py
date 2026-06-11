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
        config_path=Path(__file__).parent / "config.toml",
        model="gpt-5-mini",
        # model="gemini-2.5-flash",
        default_task="Search for the current weather in Munich",
        default_website="https://www.accuweather.com/",
        viewport={
            "width": 1536,
            "height": 1024
        },
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
