"""Orchestration primitives: parallel candidates, adversarial review, failover.

Three concerns live here so the engine stays readable:

``CandidatePool``
    Runs several workers in parallel, each with a different angle, and lets
    the engine pick the winner. The point is not speed - it is *diversity*.
    One worker in one mood produces one artifact; three workers with three
    different angles produce three candidate artifacts, and the engine keeps
    the one that satisfies the machine checks.

``AdversarialReview``
    Runs several verifiers with different personas against the same
    artifact and aggregates their verdicts. A single verifier can be
    agreeable; three verifiers that are told to attack the artifact from
    different angles are harder to fool.

``ProviderChain``
    Falls back to the next configured backend when the current one fails.
    A loop that dies because one endpoint returned 502 is not a closed loop,
    it is an outage.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Angles used to diversify parallel workers. The worker prompt gets one of
# these as an extra instruction so the candidates are genuinely different
# attempts instead of N samples of the same attempt.
CANDIDATE_ANGLES: Tuple[str, ...] = (
    "direct",
    "thorough",
    "concise",
    "structured",
    "example_first",
    "risk_first",
)

# Personas used by the adversarial reviewers. Each one is told to attack the
# artifact from a different angle.
REVIEWER_PERSONAS: Tuple[str, ...] = (
    "strict",
    "skeptic",
    "completeness",
    "reader",
)

ANGLE_INSTRUCTIONS: Dict[str, str] = {
    "direct": "直接给出交付物本身，不要铺垫。",
    "thorough": "追求完整与严谨，覆盖所有验收标准要求的内容。",
    "concise": "追求精简，信息密度高，不写废话。",
    "structured": "使用清晰的结构与标题组织交付物。",
    "example_first": "以具体示例或实例开头，再展开说明。",
    "risk_first": "优先指出并处理风险与边界情况，再给主结论。",
}

PERSONA_INSTRUCTIONS: Dict[str, str] = {
    "strict": "你是严格的标准审查者，默认怀疑，找不到证据就判不通过。",
    "skeptic": "你是怀疑论者，专找交付物里站不住脚的说法与未验证的断言。",
    "completeness": "你是完整性审查者，专查是否遗漏了验收标准要求的任何部分。",
    "reader": "你是目标读者代理，从读者视角判断这份交付物是否真的可用。",
}


def angle_instruction(angle: str) -> str:
    return ANGLE_INSTRUCTIONS.get(angle, ANGLE_INSTRUCTIONS["direct"])


def persona_instruction(persona: str) -> str:
    return PERSONA_INSTRUCTIONS.get(persona, PERSONA_INSTRUCTIONS["strict"])


def pick_angles(count: int, first: str = "") -> List[str]:
    """Return ``count`` distinct angles, cycling when count exceeds the pool.

    ``first`` anchors the rotation so a caller can request a specific
    perspective (for example ``thorough``) and still get a diverse pool
    around it instead of always starting at ``direct``.
    """
    count = max(1, int(count))
    start = CANDIDATE_ANGLES.index(first) if first and first in CANDIDATE_ANGLES else 0
    return [CANDIDATE_ANGLES[(start + i) % len(CANDIDATE_ANGLES)] for i in range(count)]


def pick_personas(count: int) -> List[str]:
    count = max(1, int(count))
    return [REVIEWER_PERSONAS[i % len(REVIEWER_PERSONAS)] for i in range(count)]


def run_parallel(
    tasks: Sequence[Callable[[int], Any]],
    workers: Optional[int] = None,
) -> List[Any]:
    """Run ``tasks`` concurrently and return their results in order.

    Each task receives its index. Exceptions are captured and returned as
    ``_ParallelFailure`` wrappers instead of propagating, so one broken
    candidate never takes down the whole pool.
    """
    items = list(tasks)
    if not items:
        return []
    if len(items) == 1:
        return [_guard(items[0], 0)]
    max_workers = workers or min(len(items), 8)
    results: List[Any] = [None] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_guard, task, index): index for index, task in enumerate(items)}
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            results[index] = future.result()
    return results


class ParallelFailure:
    """Wrapper marking a task that raised."""

    def __init__(self, index: int, error: BaseException) -> None:
        self.index = index
        self.error = error

    @property
    def message(self) -> str:
        return f"{type(self.error).__name__}: {self.error}"

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ParallelFailure {self.index} {self.message}>"


def _guard(task: Callable[[int], Any], index: int) -> Any:
    try:
        return task(index)
    except BaseException as exc:  # noqa: BLE001 - pool must survive one failure
        return ParallelFailure(index, exc)


def aggregate_reviews(
    reviews: List[Dict[str, Any]],
    mode: str = "all",
) -> Dict[str, Any]:
    """Aggregate several verifier verdicts into one.

    ``mode="all"``
        A criterion passes only when every reviewer passed it. This is the
        strict reading: one dissenting reviewer blocks the closure.

    ``mode="majority"``
        A criterion passes when the majority of reviewers passed it.

    ``mode="any"``
        A criterion passes when at least one reviewer passed it. Use with
        care - it is the weakest guarantee.

    Reviewers that failed to produce a verdict are ignored, but they are
    reported in ``skipped`` so the caller can decide whether to trust the
    aggregate.
    """
    usable = [r for r in reviews if isinstance(r, dict) and r.get("scores")]
    skipped = [r for r in reviews if not (isinstance(r, dict) and r.get("scores"))]
    if not usable:
        return {
            "scores": [],
            "verdict": "fail",
            "issues": ["no usable reviewer verdict"],
            "reviewers": len(reviews),
            "usable": 0,
            "skipped": len(skipped),
            "mode": mode,
        }

    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for review in usable:
        for score in review.get("scores") or []:
            if isinstance(score, dict) and score.get("id"):
                by_id.setdefault(str(score["id"]), []).append(score)

    merged: List[Dict[str, Any]] = []
    for criterion_id, votes in by_id.items():
        passed_votes = [v for v in votes if v.get("pass")]
        if mode == "any":
            passed = bool(passed_votes)
        elif mode == "majority":
            passed = len(passed_votes) * 2 > len(votes)
        else:
            passed = len(passed_votes) == len(votes)
        notes = [str(v.get("note", "")).strip() for v in votes if v.get("note")]
        if passed:
            note = "; ".join(notes) if notes else f"{len(passed_votes)}/{len(votes)} reviewers passed"
        else:
            dissent = [str(v.get("note", "")).strip() for v in votes if not v.get("pass")]
            note = "; ".join(dissent) if dissent else f"only {len(passed_votes)}/{len(votes)} reviewers passed"
        merged.append(
            {
                "id": criterion_id,
                "pass": passed,
                "note": note,
                "source": "review",
                "votes": f"{len(passed_votes)}/{len(votes)}",
            }
        )

    verdict = "pass" if merged and all(m["pass"] for m in merged) else "fail"
    issues: List[str] = []
    for review in usable:
        for issue in review.get("issues") or []:
            text = str(issue)
            if text not in issues:
                issues.append(text)
    return {
        "scores": merged,
        "verdict": verdict,
        "issues": issues,
        "reviewers": len(reviews),
        "usable": len(usable),
        "skipped": len(skipped),
        "mode": mode,
    }


class ProviderChain:
    """Try providers in order until one answers.

    The chain keeps the first provider as the primary one so the common
    single-provider setup behaves exactly like v0.1.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        self.providers = list(providers)
        self.calls = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.providers[0].name if self.providers else "none"

    @property
    def model(self) -> str:
        return self.providers[0].model if self.providers else ""

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "calls": self.calls,
            "chain": [f"{p.name}/{p.model}" for p in self.providers],
        }

    def complete(self, **kwargs: Any) -> Dict[str, Any]:
        if not self.providers:
            raise RuntimeError("provider chain is empty")
        last_error: Optional[BaseException] = None
        for provider in self.providers:
            try:
                response = provider.complete(**kwargs)
            except BaseException as exc:  # noqa: BLE001 - try the next backend
                last_error = exc
                continue
            with self._lock:
                self.calls += 1
            response.setdefault("provider_used", provider.name)
            response.setdefault("model_used", provider.model)
            return response
        raise last_error if last_error else RuntimeError("all providers failed")
