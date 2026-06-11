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
        # from AGENT_S_PROVIDER below; --model is this value.
        default_model="gpt-4o",
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
