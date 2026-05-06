from dataclasses import dataclass
from typing import Literal

Platform = Literal["web", "mobile", "desktop", "cross_platform"]
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


AGENT_REGISTRY: dict[str, AgentConfig] = {
    "seeact": AgentConfig(
        name="seeact",
        platforms={"web"},
        modality="multimodal",
        run_method="python",
        env_name="seeact",
        runner_script="agents/seeact/runner.py",
        default_model="gpt-4o",
        extra_env={},
    ),
    "mobilerun": AgentConfig(
        name="mobilerun",
        platforms={"mobile"},
        modality="multimodal",
        run_method="cli",
        env_name="mobilerun",
        runner_script="agents/mobilerun/runner.py",
        default_model="gemini-2.5-pro",
        extra_env={},
    ),
    "agent_s": AgentConfig(
        name="agent_s",
        platforms={"desktop", "cross_platform"},
        modality="multimodal",
        run_method="cli",
        env_name="agents/agent_s/venv",
        runner_script="agents/agent_s/runner.py",
        default_model="gpt-4o",
        extra_env={"OCR_SERVER_ADDRESS": "http://localhost:8000"},
    ),
}


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
