"""Role definitions and prompt construction."""

from __future__ import annotations

import json
from typing import Any, Dict

#: How much of the previous deliverable is carried into the next
#: attempt's prompt.
#:
#: The previous deliverable is what the next attempt has to improve
#: on, so it has to be carried. But it is *text*, and text is the one
#: thing that can grow without a ceiling: eight attempts of a long
#: document would put eight copies of it into one prompt. So the
#: carried text is capped - and the cap is applied to the *tail*,
#: because the tail of a deliverable is where the newest work is.
PREVIOUS_ARTIFACT_CHARS = 12000

#: The same cap for the reflector's own summary.
REFLECTION_CHARS = 4000

#: The same cap for the advisor's own words.
#:
#: The advisor's reason travels into the next prompt twice - once in
#: the readable brief and once in the raw state block - and an advisor
#: that writes an essay would put both copies into every later call.
#: The text is clipped; the structured list of judgements is kept as
#: it is, because dropping one would drop a verdict rather than a
#: repetition.
LEAD_ADVICE_CHARS = 4000


def clip_text(text: str, limit: int) -> str:
    """The last ``limit`` characters of ``text``, with a marker.

    The head is dropped, not the tail. The tail of a deliverable is
    where the newest work is, and the tail of a reflection is where
    the concrete next step usually is. A truncation that kept the
    wrong end would drop exactly the part the next attempt needs.

    The marker says how much was dropped: a silently shortened
    deliverable would read as "this is all of it", and the next
    attempt would think the work really was that short.
    """
    body = str(text or "")
    if limit <= 0 or len(body) <= limit:
        return body
    dropped = len(body) - limit
    return "...（前面 %d 字已省略 / %d earlier characters omitted）\n%s" % (
        dropped,
        dropped,
        body[-limit:],
    )


def _tail(text: str, limit: int) -> str:
    """The tail of ``text``, capped at ``limit`` characters.

    A thin wrapper so the call sites read as "keep the tail" rather
    than as "call a clipper", and so the lookup happens at call time:
    a test that blinds the clipper has to blind this too.
    """
    return clip_text(text, limit)

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
        # The deliverable of the *previous* attempt. A resumed run
        # starts its attempt counter at one again, so the "rewrite the
        # issues above" instruction - which is keyed on the attempt
        # number - never fires, and the worker was asked for the
        # deliverable from scratch. The work that was already done was
        # in the ticket the whole time.
        previous = str(state.get("previous_artifact") or "")
        # The reflector's own words about the failure. The reflector
        # is a paid call: it is booked in the ledger, it is read by
        # the report - and it used to reach nobody. The worker was
        # told to "rewrite the deliverable to address the issues
        # above" while the issues above were a machine's one-line
        # complaint, and the reflection - the part that says *how* to
        # fix it - was dropped on the floor. The money was spent and
        # the answer was thrown away.
        reflection = state.get("reflection") or {}
        if not isinstance(reflection, dict):
            reflection = {}
        reflection_summary = str(
            reflection.get("summary") or reflection.get("reflection") or ""
        ).strip()
        reflection_issues = [
            str(item)
            for item in (reflection.get("issues") or [])
            if str(item).strip()
        ]
        # The advisor's own words about the criteria it judged.
        #
        # The advisor is a paid call: it is booked in the ledger, its
        # answer is printed in the report - and until this release it
        # reached no worker at all. The next attempt was told to
        # "rewrite the deliverable to address the issues above" while
        # the issues above still said "unjudged: the review gave no
        # verdict for this criterion" - a complaint about a *missing
        # answer*, thrown at a worker who could not possibly produce a
        # verdict. The advisor had already looked at the artifact and
        # said what was wrong with it, and that sentence was dropped.
        lead_advice = state.get("lead_advice") or {}
        if not isinstance(lead_advice, dict):
            lead_advice = {}
        lead_reason = str(
            lead_advice.get("reason") or lead_advice.get("advice") or ""
        ).strip()
        lead_decision = str(lead_advice.get("decision") or "").strip()
        lead_judged = []
        for item in lead_advice.get("judged") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            if not cid:
                continue
            met = item.get("met")
            mark = (
                "已满足 / met" if met is True
                else ("未满足 / not met" if met is False else "未判定 / undecided")
            )
            note = str(item.get("note") or "").strip()
            lead_judged.append(
                "- [%s] %s%s" % (cid, mark, ("：" + note) if note else "")
            )

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

        if reflection_summary or reflection_issues:
            # The reflector's own words. The machine's one-line
            # complaint says *that* something is wrong; the reflection
            # says *what to do about it*. The worker used to get only
            # the first one - so a paid reflector call was spent, was
            # booked in the ledger, and then reached nobody. The
            # worker was told to "fix the issues above" while the
            # issues above carried no way to fix them.
            #
            # The reflector is asked about the *previous* attempt, so
            # it reads this too: without it the reflector of the
            # second round has no memory of what the first round's
            # reflection already said, and it repeats itself.
            out.append("")
            out.append(
                "### 上一轮的复盘 / REFLECTION ON THE LAST FAILURE" if zh
                else "### REFLECTION ON THE LAST FAILURE"
            )
            if reflection_summary:
                out.append(_tail(reflection_summary, REFLECTION_CHARS))
            for item in reflection_issues:
                out.append(f"- {item}")

        if lead_decision or lead_reason or lead_judged:
            # The advisor's own words, and the criteria it judged.
            #
            # The advisor exists for exactly one situation: some
            # acceptance criterion had no verdict at all. It looks at
            # the artifact and says what is missing. That sentence used
            # to stop at the ledger - the next attempt was told to
            # "fix the issues above" while the issues above said
            # "unjudged", which is a complaint about a missing answer
            # and not a task anybody can carry out.
            #
            # The block is rendered for the worker and for the
            # reflector: both of them write the next attempt, and both
            # of them need to know what the advisor concluded. The
            # advisor's decision itself is *not* an instruction - it
            # may only stop the loop, never close it - so it is
            # rendered as an observation, not as an order.
            out.append("")
            out.append(
                "### 主线的建议 / THE ADVISOR'S ADVICE" if zh
                else "### THE ADVISOR'S ADVICE"
            )
            if lead_decision:
                out.append(
                    f"主线判断 / the advisor decided: {lead_decision}"
                )
            if lead_reason:
                out.append(_tail(lead_reason, LEAD_ADVICE_CHARS))
            if lead_judged:
                out.append("")
                for item in lead_judged:
                    out.append(item)

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
        elif role == "reflector" and artifact:
            # The reflector reflects *on this deliverable*. It used to
            # be asked what the next attempt must change without being
            # shown the thing that failed - so its advice was about a
            # document it had never read, and the advice the worker
            # then received was no better than the machine's note.
            out.append("")
            out.append("### 上一轮的交付物 / ARTIFACT UNDER REVIEW" if zh
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
        elif role == "worker" and attempt > 1 and (issues or previous):
            # The rewrite instruction is keyed on the *work the last
            # attempt did*, not on the machine's complaint. A resumed
            # run starts its attempt counter at one again and comes
            # with a previous deliverable but no issue list - and it
            # used to be told to write the deliverable from scratch.
            out.append("")
            out.append(
                "请针对上面列出的问题改写交付物。" if zh
                else "Rewrite the deliverable to address the issues above."
            )

        if role == "worker" and previous:
            # The previous deliverable, and what is expected of it. A
            # takeover is not a fresh start: the human is asking for
            # the *same* deliverable, improved. Showing the old text
            # without saying what to do with it lets the model treat
            # it as background and start over.
            out.append("")
            out.append(
                "### 上一轮的交付物 / PREVIOUS DELIVERABLE" if zh
                else "### PREVIOUS DELIVERABLE"
            )
            out.append("<<PREVIOUS>>")
            out.append(_tail(previous, PREVIOUS_ARTIFACT_CHARS))
            out.append("<<END PREVIOUS>>")
            out.append("")
            out.append(
                "这是上一轮已经交付的内容。请在此基础上改写并改进它，"
                "不要从零重写——保留仍然成立的部分，修掉上面列出的问题。"
                if zh
                else
                "This is what the previous attempt delivered. Improve it "
                "from here: keep what still holds and fix the issues "
                "listed above. Do not start over from nothing."
            )

        return "\n".join(out)
