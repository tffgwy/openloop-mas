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
        "artifact, and answer with a single JSON object and nothing else: "
        "{\"verdict\": \"pass\" or \"fail\", "
        "\"scores\": [{\"id\": \"c1\", \"pass\": true or false, "
        "\"note\": \"the concrete reason, quoting the artifact\"}], "
        "\"issues\": [\"short strings\"]}. "
        "Use exactly the key names verdict, scores, id, pass and note. "
        "One score entry per acceptance criterion, using the criterion ids you "
        "were given. Do not rename the keys and do not add other top-level keys."
    ),
    "reflector": (
        "You are the reflector. The last attempt failed. Explain precisely which "
        "criteria failed and what the next attempt must change. Answer with a "
        "single JSON object: {\"summary\": str, \"issues\": [str], \"next_action\": "
        "\"revise\"}."
    ),
    "lead": (
        "You are the lead agent. Decide whether the loop may close. You are "
        "consulted only when the machine checks and the reviewers could not "
        "decide - some acceptance criteria have no verdict at all. Judge "
        "those criteria yourself against the artifact, and say plainly which "
        "ones are met. A criterion you cannot judge must be reported as "
        "unmet, not assumed. Answer with a single JSON object: "
        "{\"decision\": \"close|revise|escalate\", \"reason\": str, "
        "\"judged\": [{\"id\": str, \"met\": bool, \"note\": str}]}. "
        "Use \"escalate\" when the remaining work needs a human."
    ),
}

ROLE_INSTRUCTIONS = {
    "planner": "Produce a plan for the goal. JSON only.",
    "worker": "Produce the deliverable now. Output only the deliverable text.",
    "verifier": "Verify the candidate artifact. JSON only.",
    "reflector": "Reflect on the failure and specify the next attempt. JSON only.",
    "lead": (
        "Decide the next action for the loop. Judge the criteria that have no "
        "verdict yet and answer with JSON only."
    ),
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

        The prompt has two parts:

        1. a human-readable task brief - the goal, the acceptance criteria,
           the issues found so far and the artifact under review;
        2. the raw state as JSON.

        The brief comes first on purpose. A real model has to understand what
        it is supposed to produce; burying the task inside a JSON blob made
        strong models answer "please paste the previous text" instead of
        doing the work. The JSON block is kept because the offline mock
        provider parses it, and because it gives a model the full state
        without the brief having to repeat every field.

        ``extra`` is an optional additional instruction appended before the
        task state. It is how the orchestration layer diversifies parallel
        workers ("take the concise angle") and how adversarial reviewers get
        their persona ("attack the artifact as a skeptic").
        """
        blocks = [ROLE_INSTRUCTIONS.get(role, "Answer in the requested format.")]
        blocks.append("")
        blocks.append(self._brief(role, state))
        if extra:
            blocks.append("")
            blocks.append("## EXTRA INSTRUCTION")
            blocks.append(str(extra))
        blocks.append("")
        blocks.append("## TASK STATE (raw)")
        blocks.append("<<MAS_STATE>>")
        blocks.append(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        blocks.append("<<END>>")
        return "\n".join(blocks)

    # ------------------------------------------------------------------ brief
    def _brief(self, role: str, state: Dict[str, Any]) -> str:
        """Render the task state as prose a model can act on directly."""
        zh = self.language == "zh"
        goal = str(state.get("goal") or "").strip()
        attempt = int(state.get("attempt") or 1)
        max_iterations = int(state.get("max_iterations") or 0)
        issues = [str(item) for item in (state.get("issues") or []) if str(item).strip()]
        human_note = str(state.get("human_note") or "").strip()
        plan = state.get("plan") or {}
        checks = state.get("checks") or []
        artifact = str(state.get("candidate_artifact") or "")

        out: list[str] = []

        if role == "planner":
            out.append(
                "把下面的目标拆解成一个简短、有序的执行计划，并指出最可能出错的假设。"
                if zh else
                "Break the goal below into a short ordered plan and state the "
                "riskiest assumption."
            )
        elif role == "worker":
            out.append(
                "下面是需要你完成的交付任务。直接输出交付物正文本身，"
                "不要输出计划、不要解释你将要做什么。"
                if zh else
                "Here is the deliverable you must produce. Output the "
                "deliverable itself - no plan, no meta-commentary."
            )
            # The write protocol is part of the worker's instructions, not
            # an afterthought: without it the worker describes the file it
            # *would* write, and the file check - which reads the disk -
            # fails on every iteration.
            write_note = str(state.get("write_instruction") or "")
            if write_note:
                out.append(write_note.strip())
        elif role == "verifier":
            out.append(
                "下面是一份待评审的交付物和对应的验收标准。逐条判断并引用原文证据。"
                if zh else
                "Below is an artifact to review against its acceptance "
                "criteria. Judge every criterion and cite concrete evidence."
            )
        elif role == "reflector":
            out.append(
                "上一轮尝试没有通过验收。说明具体是哪几条没通过，以及下一轮必须改什么。"
                if zh else
                "The previous attempt failed. Say precisely which criteria "
                "failed and what the next attempt must change."
            )
        elif role == "lead":
            out.append(
                "下面有一些验收标准既没有机检结果，评审也没有给出判定。"
                "请对照交付物自己逐条判断，判断不了的如实说判断不了，"
                "然后决定这个循环是否可以结束。"
                if zh else
                "Some acceptance criteria have neither a machine check nor a "
                "verdict. Judge them yourself against the artifact, say "
                "plainly which ones you cannot judge, and then decide whether "
                "this loop may close."
            )

        out.append("")
        out.append("### 目标 / GOAL" if zh else "### GOAL")
        out.append(goal or "(未提供)")
        if attempt > 1 and max_iterations:
            out.append("")
            out.append(
                f"这是第 {attempt}/{max_iterations} 次尝试。" if zh
                else f"This is attempt {attempt} of {max_iterations}."
            )

        criteria = state.get("criteria") or []
        if criteria:
            out.append("")
            out.append("### 验收标准 / ACCEPTANCE CRITERIA" if zh
                       else "### ACCEPTANCE CRITERIA")
            for item in criteria:
                if not isinstance(item, dict):
                    out.append(f"- {item}")
                    continue
                cid = str(item.get("id") or "")
                desc = str(item.get("desc") or "")
                required = item.get("required", True)
                suffix = "" if required else ("（可选 / optional）" if zh else " (optional)")
                out.append(f"- [{cid}] {desc}{suffix}")
                check = item.get("check")
                if isinstance(check, dict) and check:
                    kind = str(check.get("type") or "")
                    arg = check.get("arg")
                    if kind and arg not in (None, "", []):
                        rendered = ", ".join(str(a) for a in arg) if isinstance(arg, list) else str(arg)
                        out.append(
                            f"  - 机检规则 / machine check: {kind} -> {rendered}"
                        )

        if plan:
            summary = ""
            steps: list[str] = []
            if isinstance(plan, dict):
                summary = str(plan.get("summary") or "")
                raw_steps = plan.get("plan") or []
                steps = [str(s) for s in raw_steps if str(s).strip()]
            if summary or steps:
                out.append("")
                out.append("### 已有的计划 / EXISTING PLAN" if zh
                           else "### EXISTING PLAN")
                if summary:
                    out.append(summary)
                for step in steps:
                    out.append(f"- {step}")

        if issues:
            out.append("")
            out.append("### 上一轮未通过的问题 / ISSUES TO FIX" if zh
                       else "### ISSUES TO FIX")
            for issue in issues:
                out.append(f"- {issue}")

        if human_note:
            out.append("")
            out.append("### 人类补充说明 / HUMAN NOTE" if zh
                       else "### HUMAN NOTE")
            out.append(human_note)

        if role == "lead" and artifact:
            # The lead is consulted when the engine could not decide on
            # its own. It can only judge what it can see: the artifact,
            # the per-criterion results, and - above all - which criteria
            # nobody has judged. Without the artifact the lead would be
            # guessing, and a guess that closes the loop is worse than
            # no lead at all.
            out.append("")
            out.append("### 待判定的标准 / UNJUDGED CRITERIA" if zh
                       else "### UNJUDGED CRITERIA")
            for item in state.get("scores") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("source") != "model-missing":
                    continue
                out.append(
                    f"- [{item.get('id')}] 待判定 / unjudged"
                    f"（{item.get('note', '') or '评审未给出判定'}）"
                )
            out.append("")
            out.append("### 交付物 / ARTIFACT" if zh else "### ARTIFACT")
            out.append("<<ARTIFACT>>")
            out.append(artifact)
            out.append("<<END ARTIFACT>>")

        if role == "verifier" and artifact:
            out.append("")
            out.append("### 待评审交付物 / ARTIFACT UNDER REVIEW" if zh
                       else "### ARTIFACT UNDER REVIEW")
            out.append("<<ARTIFACT>>")
            out.append(artifact)
            out.append("<<END ARTIFACT>>")
            if checks:
                out.append("")
                out.append("### 机检结果 / MACHINE CHECK RESULTS" if zh
                           else "### MACHINE CHECK RESULTS")
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    # Three states, not two. A check that could not decide
                    # anything (no rule, a broken rule) is neither a pass
                    # nor a fail - showing it as FAIL made the reviewer
                    # read "the machine check failed" for a criterion that
                    # was simply never checked.
                    raw = check.get("pass")
                    if raw is None:
                        mark = "UNDECIDED"
                    elif raw:
                        mark = "PASS"
                    else:
                        mark = "FAIL"
                    out.append(
                        f"- [{check.get('id')}] {mark} - {check.get('note', '')}"
                    )
        elif role == "worker" and attempt > 1 and issues:
            out.append("")
            out.append(
                "请针对上面列出的问题改写交付物。" if zh
                else "Rewrite the deliverable to address the issues above."
            )

        return "\n".join(out)
