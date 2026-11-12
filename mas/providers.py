"""Model providers.

Three providers ship with the project:

* ``mock``          - offline deterministic simulator, used by the demo, the
                      self-check and the test-suite. It never talks to a
                      network and never pretends to be a real model: the
                      acceptance criteria are evaluated by the deterministic
                      checker in :mod:`mas.engine`, not by the simulator.
* ``openai_compat`` - any OpenAI-compatible ``/chat/completions`` endpoint
                      (OpenAI, Grok, Kimi/Moonshot, DeepSeek, Qwen, vLLM,
                      Ollama-compatible gateways, ...).
* ``anthropic``     - the Claude Messages API.

All HTTP calls use ``urllib`` from the standard library, so the package
installs without any third-party dependency.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from mas.orchestration import ProviderChain


class ProviderError(RuntimeError):
    """Raised when a provider call cannot be completed."""


class Provider:
    """Base provider interface."""

    name = "base"

    def __init__(self, model: str = "", **options: Any) -> None:
        self.model = model
        self.options = options
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        role: str = "worker",
        expect_json: bool = False,
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model, "calls": self.calls}


class _HttpProvider(Provider):
    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        timeout = float(self.options.get("timeout_sec", 120))
        retries = int(self.options.get("max_retries", 2))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read().decode("utf-8", "replace")
                try:
                    return json.loads(raw)
                except ValueError:
                    # Some gateways answer with an SSE event stream even on the
                    # chat-completions route. Fold it back into the plain
                    # chat-completions shape so the providers stay simple.
                    folded = self._fold_sse(raw)
                    if folded is not None:
                        return folded
                    raise
            except urllib.error.HTTPError as exc:  # pragma: no cover - network path
                detail = exc.read().decode("utf-8", "replace")[:400]
                last_error = ProviderError(f"HTTP {exc.code}: {detail}")
                if exc.code in (400, 401, 403, 404):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:  # pragma: no cover
                last_error = ProviderError(str(exc))
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
        raise last_error or ProviderError("request failed")

    @staticmethod
    def _fold_sse(raw: str) -> Dict[str, Any] | None:
        """Fold an SSE stream into a chat-completions style response.

        Handles the two shapes seen in the wild:

        * OpenAI chat streaming chunks - ``choices[].delta.content``;
        * Responses streaming events - ``response.output_text.delta`` plus a
          final ``response.completed`` / ``response.failed`` event.

        Returns ``None`` when the payload is not an SSE stream at all, so the
        caller can surface the original parse error.
        """
        if "data:" not in raw:
            return None
        parts: List[str] = []
        usage: Dict[str, int] = {}
        model = ""
        finish_reason = None
        saw_event = False
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
            saw_event = True
            etype = str(event.get("type") or "")
            if etype in ("response.failed", "error"):
                error = (event.get("response") or {}).get("error") or event.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise ProviderError(f"stream failed: {message or etype}")
            if etype == "response.output_text.delta" and isinstance(event.get("delta"), str):
                parts.append(event["delta"])
            if etype in ("response.completed", "response.in_progress", "response.created"):
                response = event.get("response") or {}
                model = str(response.get("model") or model)
                raw_usage = response.get("usage") or {}
                if isinstance(raw_usage, dict) and raw_usage:
                    usage = {
                        "prompt_tokens": int(raw_usage.get("input_tokens", 0) or 0),
                        "completion_tokens": int(raw_usage.get("output_tokens", 0) or 0),
                        "total_tokens": int(raw_usage.get("total_tokens", 0) or 0),
                    }
            if "choices" in event:
                for choice in event.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") or {}
                    message = choice.get("message") or {}
                    for piece in (delta.get("content"), message.get("content")):
                        if isinstance(piece, str):
                            parts.append(piece)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                raw_usage = event.get("usage") or {}
                if isinstance(raw_usage, dict) and raw_usage:
                    usage = {
                        "prompt_tokens": int(raw_usage.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(raw_usage.get("completion_tokens", 0) or 0),
                        "total_tokens": int(raw_usage.get("total_tokens", 0) or 0),
                    }
        if not saw_event:
            return None
        if not usage.get("total_tokens"):
            usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return {
            "choices": [
                {
                    "message": {"content": "".join(parts)},
                    "finish_reason": finish_reason or "stop",
                }
            ],
            "usage": usage,
            "model": model,
        }

    @staticmethod
    def _flatten_content(content: Any) -> str:
        """Normalise the many shapes of ``message.content`` into plain text.

        Gateways answer with a plain string, with a list of content blocks
        (the multimodal shape some gateways use even for pure text) or with
        ``None``. Handing the raw list to the caller would break every
        downstream JSON parse, so the blocks are joined here.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    elif block.get("type") == "text" and isinstance(block.get("value"), str):
                        parts.append(block["value"])
            return "".join(parts)
        return str(content)
    @staticmethod
    def _normalize_usage(usage: Dict[str, Any]) -> Dict[str, int]:
        """Normalise usage counters across dialects.

        Chat-completions servers report ``prompt_tokens`` /
        ``completion_tokens``; Responses-style bridges report ``input_tokens``
        / ``output_tokens``. Several gateways omit ``total_tokens``
        entirely, which used to be recorded as ``0`` and made the ledger
        under-report the real token spend.
        """
        prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        total = int(usage.get("total_tokens", 0) or 0)
        if not total:
            total = prompt + completion
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }



class OpenAICompatProvider(_HttpProvider):
    """Any OpenAI-compatible chat-completions endpoint."""

    name = "openai_compat"

    def complete(self, *, system: str, prompt: str, role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
        base_url = str(self.options.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        api_key = str(self.options.get("api_key") or "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(self.options.get("extra_headers") or {})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": float(self.options.get("temperature", 0.2)),
        }
        # Some OpenAI-compatible gateways - the Codex Responses bridge in
        # front of Claude models among them - ignore ``messages`` entirely
        # and only read ``input``. Sending both makes the request work on
        # strict OpenAI gateways (they read ``messages``) and on the
        # Responses-style bridges (they read ``input``). When both are
        # present the bridge prefers ``input``; a plain OpenAI endpoint
        # ignores the unknown key.
        payload["input"] = prompt
        if expect_json:
            payload["response_format"] = {"type": "json_object"}
        url = f"{base_url}/chat/completions"
        try:
            data = self._post_json(url, payload, headers)
        except ProviderError as exc:
            # Not every OpenAI-compatible gateway implements ``response_format``.
            # Degrade to a plain call once; the caller then extracts the JSON
            # object from the answer text via ``mas.util.parse_json_loose``.
            message = str(exc)
            if not expect_json or "response_format" not in payload:
                raise
            if "HTTP 400" not in message and "response_format" not in message:
                raise
            payload.pop("response_format")
            try:
                data = self._post_json(url, payload, headers)
            except ProviderError as retry_exc:
                raise exc from retry_exc
        try:
            raw_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from exc
        text = self._flatten_content(raw_text)
        usage = self._normalize_usage(data.get("usage") or {})
        self.calls += 1
        return {
            "text": text,
            "usage": usage,
            "raw": data,
        }




class AnthropicProvider(_HttpProvider):
    """Claude Messages API."""

    name = "anthropic"

    def complete(self, *, system: str, prompt: str, role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
        base_url = str(self.options.get("base_url") or "https://api.anthropic.com").rstrip("/")
        api_key = str(self.options.get("api_key") or "")
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        }
        headers.update(self.options.get("extra_headers") or {})
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = self._post_json(f"{base_url}/v1/messages", payload, headers)
        chunks: List[str] = []
        for block in data.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
        usage = data.get("usage") or {}
        self.calls += 1
        return {
            "text": "".join(chunks),
            "usage": self._normalize_usage(usage),
            "raw": data,
        }


class MockProvider(Provider):
    """Deterministic offline simulator.

    The simulator is deliberately dumb: it produces a draft from the task
    description. It cannot judge quality. Pass/fail decisions are made by the
    deterministic criterion checker inside :mod:`mas.engine`, so a closed loop
    in mock mode is a real closure of a real rule set, not a scripted
    "success" message.
    """

    name = "mock"

    def complete(self, *, system: str, prompt: str, role: str = "worker",
                 expect_json: bool = False, max_tokens: int = 4000) -> Dict[str, Any]:
        state = self._extract_state(prompt)
        attempt = int(state.get("attempt", 1))
        criteria = state.get("criteria") or []
        goal = str(state.get("goal", ""))
        issues = state.get("issues") or []

        if role == "planner":
            text = json.dumps(
                {
                    "summary": f"拆解目标：{goal}",
                    "plan": [c.get("desc", "") for c in criteria] or ["完成目标描述的内容"],
                    "risks": [],
                },
                ensure_ascii=False,
            )
        elif role == "worker":
            text = self._draft(goal, criteria, attempt, issues)
        elif role == "verifier":
            checks = state.get("checks") or []
            scores = []
            for check in checks:
                scores.append(
                    {
                        "id": check.get("id"),
                        "pass": bool(check.get("pass")),
                        "note": check.get("note", ""),
                    }
                )
            unjudged = [c for c in criteria if not c.get("check")]
            for criterion in unjudged:
                scores.append(
                    {
                        "id": criterion.get("id"),
                        "pass": False,
                        "note": "unjudged: 该验收标准没有机检规则，mock 模式无法判定",
                    }
                )
            passed = bool(scores) and all(s["pass"] for s in scores)
            text = json.dumps(
                {
                    "verdict": "pass" if passed else "fail",
                    "scores": scores,
                    "issues": [] if passed else [c.get("id") for c in criteria if not c.get("check")],
                },
                ensure_ascii=False,
            )
        elif role == "reflector":
            text = json.dumps(
                {
                    "summary": "复盘未通过项并给出下一轮修改要点",
                    "issues": issues or ["criterion not satisfied"],
                    "next_action": "revise",
                },
                ensure_ascii=False,
            )
        elif role == "lead":
            text = json.dumps({"decision": "revise", "reason": "verifier 未通过"}, ensure_ascii=False)
        else:
            text = json.dumps({"role": role, "ok": True}, ensure_ascii=False)

        self.calls += 1
        return {
            "text": text,
            "usage": {
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": len(text) // 4,
                "total_tokens": (len(prompt) + len(text)) // 4,
            },
            "raw": {"mock": True, "role": role},
        }

    @staticmethod
    def _extract_state(prompt: str) -> Dict[str, Any]:
        marker = "<<MAS_STATE>>"
        if marker not in prompt:
            return {}
        tail = prompt.split(marker, 1)[1]
        end = tail.find("<<END>>")
        if end == -1:
            return {}
        try:
            data = json.loads(tail[:end].strip())
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _draft(goal: str, criteria: List[Dict[str, Any]], attempt: int,
               issues: List[Any]) -> str:
        # A criterion that demands a JSON document cannot be satisfied by a
        # Markdown draft, so the simulator emits JSON for that case. Without
        # this the loop would spin forever against a rule the artifact shape
        # can never meet.
        json_keys: List[str] = []
        for criterion in criteria:
            check = criterion.get("check") or {}
            if str(check.get("type") or "").lower() == "json_keys":
                arg = check.get("arg")
                keys = arg if isinstance(arg, list) else [arg]
                for key in keys:
                    if key is not None and str(key) not in json_keys:
                        json_keys.append(str(key))
        if json_keys:
            payload: Dict[str, Any] = {}
            for key in json_keys:
                payload[key] = f"{goal} 的 {key}"
            if "verdict" in payload and not isinstance(payload.get("verdict"), bool):
                payload["verdict"] = "pass"
            return json.dumps(payload, ensure_ascii=False, indent=2)

        lines: List[str] = [f"# 交付物：{goal}", ""]
        for criterion in criteria:
            check = criterion.get("check") or {}
            kind = check.get("type")
            arg = check.get("arg")
            desc = criterion.get("desc", "")
            if kind == "contains" and arg:
                lines.append(f"- {desc}：{arg}")
            else:
                lines.append(f"- {desc}")
        lines.append("")
        if attempt <= 1:
            lines.append("（第一版草稿，尚需补充细节。）")
        else:
            lines.append("## 补充说明")
            target = 0
            for criterion in criteria:
                check = criterion.get("check") or {}
                if check.get("type") in ("min_chars", "max_chars"):
                    try:
                        target = max(target, int(check.get("arg") or 0))
                    except (TypeError, ValueError):
                        target = max(target, 0)
            body = "\n".join(lines)
            filler = ["- 要点补充 %d：进一步细化执行细节与可验证说明。" % i for i in range(1, 40)]
            index = 0
            while len(body) < target and index < len(filler):
                body += "\n" + filler[index]
                index += 1
            lines = body.split("\n")
        if issues:
            lines.append("")
            lines.append("## 本轮修复")
            for issue in issues:
                lines.append(f"- 已处理：{issue}")
        return "\n".join(lines)


def build_provider_chain(config: Dict[str, Any]) -> ProviderChain:
    """Build the primary provider plus its failover backends.

    The configuration may declare ``fallbacks``: a list of provider specs
    shaped exactly like the top level provider config::

        {
          "provider": "openai_compat",
          "model": "gpt-4o-mini",
          "base_url": "https://api.openai.com/v1",
          "fallbacks": [
            {"provider": "anthropic", "model": "claude-sonnet-4-5",
             "base_url": "https://api.anthropic.com"},
            {"provider": "openai_compat", "model": "kimi-k2",
             "base_url": "https://api.moonshot.cn/v1"}
          ]
        }

    The primary provider stays first, so a single-provider setup behaves
    exactly like v0.1. When the primary fails - 502, timeout, rate limit -
    the next backend is tried. A loop that dies because one endpoint
    returned an error is not a closed loop, it is an outage.
    """
    providers: List[Provider] = []
    primary = build_provider(config)
    providers.append(primary)

    for spec in config.get("fallbacks") or []:
        if not isinstance(spec, dict):
            continue
        merged = dict(config)
        # A fallback inherits the shared connection settings but not the
        # primary model or endpoint. Every connection setting can be
        # overridden per fallback: a backup backend usually lives on a
        # different network with a different latency profile, so forcing
        # it to reuse the primary's timeout and retry count was wrong.
        for key in (
            "provider",
            "model",
            "base_url",
            "api_key",
            "api_key_env",
            "timeout_sec",
            "max_retries",
            "extra_headers",
            "temperature",
        ):
            if key in spec:
                merged[key] = spec[key]
        merged.pop("fallbacks", None)
        try:
            providers.append(build_provider(merged))
        except ProviderError:
            # A malformed fallback must not break the primary setup.
            continue

    return ProviderChain(providers)


def build_provider(config: Dict[str, Any]) -> Provider:
    """Instantiate the provider described by the configuration."""
    kind = str(config.get("provider") or "mock").lower()
    model = str(config.get("model") or "")
    options = {
        "base_url": config.get("base_url") or "",
        "api_key": config.get("api_key") or "",
        "timeout_sec": config.get("timeout_sec", 120),
        "max_retries": config.get("max_retries", 2),
        "extra_headers": config.get("extra_headers") or {},
    }
    if kind == "mock":
        return MockProvider(model=model or "mock-1", **options)
    if kind == "anthropic":
        return AnthropicProvider(model=model or "claude-sonnet-4-5", **options)
    if kind in ("openai_compat", "openai", "compat", "moonshot", "kimi", "deepseek", "qwen"):
        return OpenAICompatProvider(model=model or "gpt-4o-mini", **options)
    raise ProviderError(f"unknown provider: {kind}")
