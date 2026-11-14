"""Live endpoint probe.

``mas doctor`` used to stop at "the configuration file looks fine". A
beginner then discovers that their gateway speaks a slightly different
dialect only when the first real run fails. This module performs the
cheapest possible real request and reports *which* dialect the endpoint
speaks, so the diagnosis happens before the first deliverable instead of
during it.

The probe never writes to the ledger and never counts against a run
budget; it is a handshake, not a run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# Dialect identifiers reported by the probe.
DIALECT_OPENAI_CHAT = "openai-chat-completions"
DIALECT_SSE_CHAT = "openai-sse-stream"
DIALECT_RESPONSES_EVENTS = "openai-responses-events"
DIALECT_ANTHROPIC = "anthropic-messages"
DIALECT_UNKNOWN = "unknown"

# The marker the handshake uses to prove that the *system* instruction
# actually reached the model. A gateway that answers "pong" while ignoring
# the system instruction is worse than a gateway that is plainly broken:
# it looks healthy, and every role contract in the loop silently stops
# applying. The marker is unusual on purpose - a real answer is very
# unlikely to contain it by accident.
ECHO_MARKER = "MASPROBE7Q"


def _detect_dialect(raw: str) -> str:
    """Classify a raw response body into a dialect identifier."""
    stripped = raw.strip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            if "choices" in data:
                return DIALECT_OPENAI_CHAT
            if "content" in data and isinstance(data.get("content"), list):
                return DIALECT_ANTHROPIC
            if "output" in data:
                return DIALECT_RESPONSES_EVENTS
        return DIALECT_UNKNOWN

    if "data:" in raw:
        saw_responses_event = False
        saw_choice_chunk = False
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                event = json.loads(chunk)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "").startswith("response."):
                saw_responses_event = True
            if "choices" in event:
                saw_choice_chunk = True
        if saw_responses_event:
            return DIALECT_RESPONSES_EVENTS
        if saw_choice_chunk:
            return DIALECT_SSE_CHAT
    return DIALECT_UNKNOWN


def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _text_from_body(raw: str) -> str:
    """Pull the answer text out of a response body.

    The handshake needs to *read* the model answer to tell whether the
    instruction it sent was applied. Three shapes have to be understood:
    a plain chat-completions body, an Anthropic messages body and an
    event stream.
    """
    stripped = raw.strip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(block["text"])
                    return "".join(parts)
            blocks = data.get("content")
            if isinstance(blocks, list):
                parts = []
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
                return "".join(parts)
            if isinstance(data.get("output_text"), str):
                return data["output_text"]
        return ""

    parts: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            event = json.loads(chunk)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("delta"), str):
            parts.append(event["delta"])
        choices = event.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta") or {}
            if isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    return "".join(parts)


def probe(
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_sec: float = 20.0,
) -> Dict[str, Any]:
    """Send one minimal request and classify the answer.

    Returns a dict with ``status`` (``ok`` / ``warn`` / ``fail``),
    ``dialect`` and a human readable ``detail``.
    """
    base = str(base_url or "").rstrip("/")
    if not base:
        return {"status": "warn", "dialect": DIALECT_UNKNOWN, "detail": "未配置 base_url"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        if provider == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            headers["x-api-key"] = api_key
            url = f"{base}/v1/messages"
            payload = {
                "model": model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            }
        else:
            url = f"{base}/chat/completions"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                # The probe must use *the same* payload shape the provider
                # uses, otherwise the handshake tests a request the
                # runtime never sends. The provider sends the whole
                # conversation in both shapes; the probe does the same.
                "input": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
            }
        raw = _post(url, payload, headers, timeout_sec)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return {
            "status": "fail",
            "dialect": DIALECT_UNKNOWN,
            "detail": f"HTTP {exc.code}: {detail}",
        }
    except urllib.error.URLError as exc:
        return {
            "status": "fail",
            "dialect": DIALECT_UNKNOWN,
            "detail": f"连接失败: {exc.reason}",
        }
    except (TimeoutError, OSError) as exc:
        return {"status": "fail", "dialect": DIALECT_UNKNOWN, "detail": f"网络错误: {exc}"}

    dialect = _detect_dialect(raw)
    if dialect == DIALECT_UNKNOWN:
        return {
            "status": "warn",
            "dialect": dialect,
            "detail": f"响应格式无法识别: {raw[:120]}",
        }
    return {
        "status": "ok",
        "dialect": dialect,
        "detail": f"端点可用，协议方言 = {dialect}",
    }


def probe_from_config(config: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    """Probe using the values of a loaded configuration."""
    provider = str(config.get("provider") or "mock")
    if provider == "mock":
        return {
            "status": "ok",
            "dialect": "mock",
            "detail": "mock（离线模式，未发起网络请求）",
        }
    key = api_key if api_key is not None else str(config.get("api_key") or "")
    return probe(
        provider=provider,
        base_url=str(config.get("base_url") or ""),
        model=str(config.get("model") or ""),
        api_key=key,
        timeout_sec=float(config.get("timeout_sec", 20) or 20),
    )


def probe_instruction_echo(
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_sec: float = 20.0,
) -> Dict[str, Any]:
    """Check that the *system* instruction actually reaches the model.

    ``probe`` only proves that the endpoint answers and which dialect it
    speaks. It cannot tell whether the system instruction was applied -
    and on a Responses-style gateway it was not: the bridge preferred
    ``input`` over ``messages``, and ``input`` used to carry only the
    user prompt. The endpoint answered happily, so ``mas doctor`` said
    "all good" while every role contract in the loop (the verifier's
    JSON schema, the worker's "output the deliverable itself") was being
    thrown away.

    So the handshake asks the model to repeat one marker that only the
    system instruction mentions, and checks the answer for it.

    Returns ``status`` ``ok`` (the instruction was applied), ``warn``
    (the endpoint answered but the instruction was *not* applied) or
    ``fail`` (the check could not be performed at all).
    """
    base = str(base_url or "").rstrip("/")
    if not base:
        return {"status": "fail", "detail": "未配置 base_url"}

    system = f"Reply with exactly this token and nothing else: {ECHO_MARKER}"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        if provider == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            headers["x-api-key"] = api_key
            url = f"{base}/v1/messages"
            payload: Dict[str, Any] = {
                "model": model,
                "max_tokens": 32,
                "system": system,
                "messages": [{"role": "user", "content": "ping"}],
            }
        else:
            url = f"{base}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "ping"},
                ],
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "ping"},
                ],
                "max_tokens": 32,
            }
        raw = _post(url, payload, headers, timeout_sec)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return {"status": "fail", "detail": f"HTTP {exc.code}: {detail}"}
    except urllib.error.URLError as exc:
        return {"status": "fail", "detail": f"连接失败: {exc.reason}"}
    except (TimeoutError, OSError) as exc:
        return {"status": "fail", "detail": f"网络错误: {exc}"}

    answer = _text_from_body(raw)
    if not answer.strip():
        return {"status": "fail", "detail": "端点未返回可读文本，无法校验指令是否生效"}
    if ECHO_MARKER in answer:
        return {
            "status": "ok",
            "detail": "角色指令已生效（系统提示被模型采纳）",
        }
    return {
        "status": "warn",
        "detail": (
            f"端点可用但未采纳系统指令（回答中没有 {ECHO_MARKER}）；"
            "角色契约可能未生效，闭环判定不可信"
        ),
    }


DIALECT_HINTS: List[str] = [
    DIALECT_OPENAI_CHAT,
    DIALECT_SSE_CHAT,
    DIALECT_RESPONSES_EVENTS,
    DIALECT_ANTHROPIC,
]
