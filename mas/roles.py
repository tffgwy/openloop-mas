"""Role definitions and prompt construction."""

from __future__ import annotations

import json
from typing import Any, Dict

DEFAULT_SYSTEM = {
    "planner": (
        "You are the planner of a multi-agent system. Decompose the goal into a "
        "short, ordered plan and state the riskiest assumption. Answer with a "
        "single JSON object: {\"summary\": str, \"plan\": [str], \"risks\": [str]}."
    ),
    "worker": (
        "You are the executor. Produce the deliverable itself - not a plan, not a "
        "description of what you would do. The artifact you output IS the "
        "deliverable that will be checked."
    ),
    "verifier": (
        "You are an independent verifier. You did not write the artifact. Judge it "
        "against every acceptance criterion, cite concrete evidence from the "
        "artifact, and answer with a single JSON object: {\"verdict\": \"pass|"
        "fail\", \"scores\": [{\"id\": str, \"pass\": bool, \"note\": str}], "
        "\"issues\": [str]}."
    ),
    "reflector": (
        "You are the reflector. The last attempt failed. Explain precisely which "
        "criteria failed and what the next attempt must change. Answer with a "
        "single JSON object: {\"summary\": str, \"issues\": [str], \"next_action\": "
        "\"revise\"}."
    ),
    "lead": (
        "You are the lead agent. Decide whether the loop may close. Answer with a "
        "single JSON object: {\"decision\": \"close|revise|escalate\", \"reason\": str}."
    ),
}

ROLE_INSTRUCTIONS = {
    "planner": "Produce a plan for the goal. JSON only.",
    "worker": "Produce the deliverable now. Output only the deliverable text.",
    "verifier": "Verify the candidate artifact. JSON only.",
    "reflector": "Reflect on the failure and specify the next attempt. JSON only.",
    "lead": "Decide the next action for the loop. JSON only.",
}


class Roles:
    """Builds prompts for the five roles of the loop."""

    def __init__(self, team: Dict[str, Any] | None = None) -> None:
        self.team = team or {}
        self.language = str(self.team.get("language") or "zh")
        overrides = self.team.get("roles") or {}
        self.systems = dict(DEFAULT_SYSTEM)
        for role, spec in overrides.items():
            if isinstance(spec, dict) and spec.get("system"):
                self.systems[role] = str(spec["system"])
            elif isinstance(spec, str):
                self.systems[role] = spec

    def system(self, role: str) -> str:
        base = self.systems.get(role, DEFAULT_SYSTEM.get(role, "You are a helpful agent."))
        if self.language == "zh":
            base += "\n请用中文输出（JSON 字段的键名保持英文）。"
        return base

    def prompt(self, role: str, state: Dict[str, Any], extra: str = "") -> str:
        """Build the prompt for a role.

        ``extra`` is an optional additional instruction appended before the
        task state. It is how the orchestration layer diversifies parallel
        workers ("take the concise angle") and how adversarial reviewers get
        their persona ("attack the artifact as a skeptic").
        """
        blocks = [ROLE_INSTRUCTIONS.get(role, "Answer in the requested format.")]
        if self.language == "zh":
            blocks.append("按下面 JSON 状态里的信息完成任务。")
        if extra:
            blocks.append("")
            blocks.append("## EXTRA INSTRUCTION")
            blocks.append(str(extra))
        blocks.append("")
        blocks.append("## TASK STATE")
        blocks.append("<<MAS_STATE>>")
        blocks.append(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        blocks.append("<<END>>")
        return "\n".join(blocks)
