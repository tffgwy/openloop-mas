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


def _detect_dialect(raw: str) -> str:
    """Classify a raw response body into a dialect identifier."""
    stripped = raw.strip()
    if stripped.startswith("{") or stripped.startswith("["):
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
                "input": "ping",
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


DIALECT_HINTS: List[str] = [
    DIALECT_OPENAI_CHAT,
    DIALECT_SSE_CHAT,
    DIALECT_RESPONSES_EVENTS,
    DIALECT_ANTHROPIC,
]
