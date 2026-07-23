from dataclasses import dataclass
from typing import Literal

Platform = Literal["web", "mobile", "desktop", "desktop_windows", "cross_platform"]
Modality = Literal["text_only", "vision_only", "multimodal"]


@dataclass
class AgentConfig:
    name:          str
    platforms:     set[Platform]
    modality:      Modality
    run_method:    Literal["python", "cli"]
    env_name:      str
    runner_script: str
    default_model: str
    extra_env:     dict
    # Set only for agents whose runner honors the --modality flag (GUI_AGENT_MODALITY).
    # Its value is the modality the runner falls back to when the flag is unset.
    # None means the agent ignores the flag entirely (its perception is fixed at
    # `modality` above); resolve_modality() then records that static capability.
    flag_default_modality: Modality | None = None


AGENT_REGISTRY: dict[str, AgentConfig] = {
    "seeact": AgentConfig(
        name="seeact",
        platforms={"web"},
        modality="multimodal",
        run_method="python",
        env_name="agents/seeact/venv",
        runner_script="agents/seeact/runner.py",
        default_model="gpt-4o",
        extra_env={},
    ),
    "mobilerun": AgentConfig(
        name="mobilerun",
        platforms={"mobile"},
        modality="multimodal",
        run_method="cli",
        env_name="agents/mobilerun/venv",
        runner_script="agents/mobilerun/runner.py",
        default_model="gemini-2.5-pro",
        extra_env={},
        # Runner reads GUI_AGENT_MODALITY; unset → accessibility-tree-only (text).
        flag_default_modality="text_only",
    ),
    "browser_use": AgentConfig(
        name="browser_use",
        platforms={"web"},
        modality="multimodal",
        # browser-use has no headless single-task CLI (its CLI only launches an
        # interactive REPL or the browser-use-web-ui Gradio server), so — like
        # seeact/agent_s — the runner drives the browser_use.Agent SDK in-process.
        run_method="python",
        env_name="agents/browser_use/venv",
        runner_script="agents/browser_use/runner.py",
        default_model="gemini-3.5-flash",
        # Provider for browser_use's bundled LLM wrappers (browser_use.llm), read by
        # runner._build_llm(); each wrapper reads its own standard API-key env var
        # so no separate key var is needed here — ChatGoogle falls back to
        # GOOGLE_API_KEY/GEMINI_API_KEY (both already in the repo .env) when no
        # api_key is passed explicitly. Telemetry is opt-out (PostHog) — disabled
        # for benchmark runs. --model must stay a Gemini model name to match this
        # provider (e.g. gemini-2.5-flash/-pro); switching provider back to
        # "openai"/"anthropic" needs a matching model name too.
        extra_env={
            "BROWSER_USE_PROVIDER": "google",
            "ANONYMIZED_TELEMETRY": "false",
        },
        # Runner reads GUI_AGENT_MODALITY and maps it to use_vision: text -> False,
        # multimodal -> True. 'vision' has no faithful implementation (browser_use
        # always addresses elements by index into its DOM/accessibility tree, so
        # that tree is never optional) and the runner raises rather than silently
        # mislabeling a multimodal run as vision_only. Unset -> multimodal, matching
        # the previous fixed behavior.
        flag_default_modality="multimodal",
    ),
    "agent_s": AgentConfig(
        name="agent_s",
        platforms={"desktop", "desktop_windows", "cross_platform"},
        modality="multimodal",
        # The runner drives gui_agents' AgentS3 in-process (the installed CLI is
        # interactive-only — no --task — so we use the SDK directly, like seeact).
        run_method="python",
        env_name="agents/agent_s/venv",
        runner_script="agents/agent_s/runner.py",
        # Planner model (gui_agents' "generation"/worker model). Its provider comes
        # from AGENT_S_PROVIDER below; --model overrides this value. Must match the
        # provider — kept on Gemini to pair with AGENT_S_PROVIDER=gemini.
        default_model="gemini-3.5-flash",
        # Agent S splits the planner (any chat model) from the grounding model
        # (turns "click Save" into pixel coords). Read by runner._build_engine_params().
        #   Phase 1 (no GPU): planner=OpenAI, grounding=Gemini via API, GROUND_URL
        #   empty. Phase 2: GROUND_PROVIDER=huggingface, GROUND_URL=<UI-TARS endpoint>,
        #   GROUND_MODEL=ui-tars-1.5-7b — no code change needed.
        # GROUNDING_WIDTH/HEIGHT are intentionally unset: for API grounding the runner
        # auto-derives them from the (scaled) screenshot so Gemini's coords map back
        # correctly; set them (e.g. 1920/1080) only for a UI-TARS endpoint in Phase 2.
        extra_env={
            "AGENT_S_PROVIDER": "openai",
            "GROUND_PROVIDER":  "gemini",
            "GROUND_MODEL":     "gemini-2.5-pro",
            "GROUND_URL":       "",
        },
    ),
    "pc_agent": AgentConfig(
        name="pc_agent",
        platforms={"desktop", "desktop_windows", "cross_platform"},
        # PC-Agent can send a SoM-annotated screenshot alongside an OCR +
        # accessibility-tree-derived text list, but the runner defaults
        # PC_AGENT_USE_PERCEPTION_INFO to off (no OCR calls at all, Aliyun or
        # local) — see agents/pc_agent/setup_notes.md — so by default it's a bare
        # screenshot, same as agent_s. Static vision_only to match; doesn't honor
        # GUI_AGENT_MODALITY (unlike mobilerun/browser_use). Set
        # PC_AGENT_USE_PERCEPTION_INFO=1 to get the hybrid representation and
        # reclassify this as multimodal for analysis.
        modality="vision_only",
        # PC-Agent (X-PLUG/MobileAgent) isn't a pip package or importable SDK —
        # run.py parses sys.argv and loads config.json at module scope, so the
        # runner shells out to the vendored script rather than driving an SDK
        # in-process (unlike seeact/agent_s/mobilerun).
        run_method="cli",
        env_name="agents/pc_agent/venv",
        runner_script="agents/pc_agent/runner.py",
        # PC-Agent's client only speaks the OpenAI chat-completions shape, but that
        # works against any OpenAI-compatible endpoint. PC_AGENT_PROVIDER=gemini
        # (runner._resolve_api_config()) routes it to Google's OpenAI-compatible
        # endpoint using GEMINI_API_KEY/GOOGLE_API_KEY — switched from the OpenAI
        # default after the configured OPENAI_API_KEY turned out to have no quota
        # (RateLimitError/insufficient_quota during integration testing).
        default_model="gemini-2.5-flash",
        extra_env={"PC_AGENT_PROVIDER": "gemini"},
    ),
    "ufo": AgentConfig(
        name="ufo",
        # Windows-only (deep UIA/Win32/WinCOM integration; primary Session class
        # is WindowsBaseSession) — unlike agent_s/pc_agent this isn't cross-platform,
        # so it doesn't claim "desktop" or "cross_platform".
        platforms={"desktop_windows"},
        modality="multimodal",
        # The runner drives ufo.module.session_pool's SessionFactory/SessionPool
        # in-process (the installed CLI, `python -m ufo`, is a separate process
        # with its own argparse/asyncio.run — no advantage over importing the
        # same classes directly, and in-process gives us the HostAgentStatus
        # object instead of having to scrape stdout/logs for it).
        run_method="python",
        env_name="agents/ufo/venv",
        runner_script="agents/ufo/runner.py",
        # Must pair with UFO_PROVIDER below — runner.py's --model override only
        # changes API_MODEL, not API_TYPE, so a mismatched pair (e.g. a gemini
        # model name with UFO_PROVIDER=openai) sends the model name to the wrong
        # provider's endpoint and 404s (confirmed via a real run).
        default_model="gemini-3.5-flash",
        # Provider for the HOST_AGENT/APP_AGENT credentials runner.py writes into
        # vendor/UFO/config/ufo/agents.yaml before each task (UFO has no env-var
        # hook for these). Matches this repo's other agents' default provider
        # (mobilerun/browser_use/agent_s/pc_agent all default to Gemini).
        extra_env={"UFO_PROVIDER": "gemini"},
        # Runner reads GUI_AGENT_MODALITY and maps it to VISUAL_MODE: text -> False,
        # multimodal -> True. 'vision' has no faithful implementation (actions
        # address UIA controls by ID from a control list that's always sent as
        # text, same situation as browser_use) and the runner raises rather than
        # silently mislabeling a multimodal run as vision_only. Unset -> multimodal.
        flag_default_modality="multimodal",
    ),
}


# The --modality flag uses runtime vocabulary; the registry/result schema uses the
# typed Modality vocabulary. Map between them so recorded values are consistent.
_FLAG_TO_MODALITY: dict[str, Modality] = {
    "text":       "text_only",
    "vision":     "vision_only",
    "multimodal": "multimodal",
}


def resolve_modality(agent: AgentConfig, flag: str | None) -> Modality:
    """The modality to record for analysis, in the typed Modality vocabulary.

    Precedence: the --modality flag when the agent honors it; otherwise (the flag
    had no effect on this agent) the agent's static capability. For a flag-honoring
    agent with the flag unset, fall back to that agent's documented default.
    """
    if agent.flag_default_modality is None:
        # Agent ignores the flag (e.g. seeact, agent_s) → record what it actually is.
        return agent.modality
    return _FLAG_TO_MODALITY.get(flag, agent.flag_default_modality)


def get_agents_for_task(
    task_platform: Platform,
    selected_agents: list[str]
) -> list[AgentConfig]:
    """Return agents from the selected list that support this task's platform."""
    return [
        AGENT_REGISTRY[name]
        for name in selected_agents
        if name in AGENT_REGISTRY
        and task_platform in AGENT_REGISTRY[name].platforms
    ]
