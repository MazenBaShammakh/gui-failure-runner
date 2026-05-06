import asyncio
import os
from seeact.agent import SeeActAgent

os.environ["OPENAI_API_KEY"] = "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"


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
