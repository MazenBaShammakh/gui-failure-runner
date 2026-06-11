import asyncio
from pathlib import Path
from mobilerun import MobileAgent, MobileConfig
from mobilerun.config_manager.config_manager import LLMProfile

# Load .env from repo root if python-dotenv is available
try:
    from dotenv import load_dotenv
    # override=True so the repo .env wins over a stale key already in the environment
    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
except ImportError:
    pass  # set OPENAI_API_KEY in your shell instead


# PROVIDER = "OpenAIResponses"   # or "Anthropic" / "GoogleGenAI"
# MODEL = "gpt-4o"
# MODEL = "gpt-5"
PROVIDER = "GoogleGenAI"   # or "Anthropic" / "OpenAIResponses"
MODEL = "gemini-2.5-flash"

openai_profile = LLMProfile(provider=PROVIDER, model=MODEL)

config = MobileConfig()
config.llm_profiles = {
    "manager":           LLMProfile(provider=PROVIDER, model=MODEL, temperature=0.2),
    "executor":          LLMProfile(provider=PROVIDER, model=MODEL, temperature=0.1),
    "fast_agent":        LLMProfile(provider=PROVIDER, model=MODEL, temperature=0.2),
    "app_opener":        LLMProfile(provider=PROVIDER, model=MODEL, temperature=0.0),
    "structured_output": LLMProfile(provider=PROVIDER, model=MODEL, temperature=0.0),
}

# Reset the device to a known state before the run. Off by default in
# mobilerun; we enable it here to try it out.
config.device.reset.enabled = True
config.device.reset.press_home = True
# Fully close (force-stop) every third-party app so nothing resumes mid-state.
# mobilerun's own Portal app is always preserved.
config.device.reset.close_all_apps = True
# Or, to close only specific apps instead, list them here (Android only):
# config.device.reset.force_stop_packages = ["com.android.settings"]


async def main():
    agent = MobileAgent(
        goal="Open Settings and check the Wi-Fi status",
        config=config,
    )

    result = await agent.run()

    print("success:", result.success)
    print("reason: ", result.reason)
    print("steps:  ", result.steps)


asyncio.run(main())
