import asyncio
import sys
from pathlib import Path

# browser-use logs emoji-heavy messages. On Windows the default console encoding
# (cp1252) raises UnicodeEncodeError on those; force UTF-8 so a smoke test doesn't
# crash on a stray glyph (same guard as the runner).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Load .env from repo root if python-dotenv is available.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
except ImportError:
    pass  # set OPENAI_API_KEY manually or export it in your shell

TASK = "Go to example.com and report the page's main heading text"
WEBSITE = "https://example.com"
# MODEL = "gemini-2.5-flash"
MODEL = "gemini-3.1-flash-lite"
MAX_STEPS = 5


async def _print_dom(agent) -> None:
    """on_step_end hook: print the indexed DOM tree the LLM saw for the step that
    just finished (agent.browser_session caches the state _prepare_context() built
    the prompt from)."""
    dom_state = agent.browser_session._cached_browser_state_summary.dom_state
    print(f"\n----- DOM after step {agent.state.n_steps} -----")
    print(dom_state.llm_representation())
    print("----- end DOM -----\n")


async def main():
    from browser_use import Agent
    from browser_use.llm import ChatGoogle

    agent = Agent(
        task=TASK,
        llm=ChatGoogle(model=MODEL),
        initial_actions=[{"navigate": {"url": WEBSITE}}],
        use_vision=True,
    )
    history = await agent.run(max_steps=MAX_STEPS, on_step_end=_print_dom)

    print("is_done:      ", history.is_done())
    print("is_successful:", history.is_successful())
    print("steps:        ", history.number_of_steps())
    print("final_result: ", history.final_result())
    print("has_errors:   ", history.has_errors())


asyncio.run(main())
