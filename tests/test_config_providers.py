"""Config and provider tests."""

import io
import json
import sys
import os
import pathlib
import shutil
import tempfile
import unittest

from mas.config import DEFAULT_CONFIG, load_config, save_config
from mas.providers import MockProvider, OpenAICompatProvider, build_provider
from mas.util import force_utf8_output, parse_json_loose, strip_reasoning


class TestConfig(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            path = save_config(config)
            self.assertTrue(path.exists())
            loaded = load_config()
            self.assertEqual(loaded["provider"], "mock")
            self.assertEqual(loaded["budget"]["max_iterations"], DEFAULT_CONFIG["budget"]["max_iterations"])

    def test_env_overrides(self):
        os.environ["MAS_PROVIDER"] = "openai_compat"
        os.environ["MAS_MODEL"] = "kimi-k2"
        try:
            loaded = load_config()
            self.assertEqual(loaded["provider"], "openai_compat")
            self.assertEqual(loaded["model"], "kimi-k2")
        finally:
            os.environ.pop("MAS_PROVIDER")
            os.environ.pop("MAS_MODEL")

    def test_partial_file_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"provider": "anthropic"}, handle)
            loaded = load_config(path)
            self.assertEqual(loaded["provider"], "anthropic")
            self.assertEqual(loaded["budget"]["max_iterations"], DEFAULT_CONFIG["budget"]["max_iterations"])


class TestProviders(unittest.TestCase):
    def test_build_provider(self):
        self.assertIsInstance(build_provider({"provider": "mock"}), MockProvider)
        self.assertIsInstance(build_provider({"provider": "openai_compat"}), OpenAICompatProvider)

    def test_mock_provider_extracts_state(self):
        provider = MockProvider()
        prompt = 'xx\n<<MAS_STATE>>\n{"attempt": 2, "goal": "g", "criteria": []}\n<<END>>'
        state = provider._extract_state(prompt)
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(state["goal"], "g")

    def test_openai_compat_payload(self):
        provider = OpenAICompatProvider(model="kimi-k2", base_url="http://127.0.0.1:1/v1", api_key="k")
        captured = {}

        def fake_post(url, payload, headers):
            captured["url"] = url
            captured["payload"] = payload
            captured["headers"] = headers
            return {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}

        provider._post_json = fake_post
        result = provider.complete(system="s", prompt="p", role="worker")
        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["payload"]["model"], "kimi-k2")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k")


class TestJsonUtil(unittest.TestCase):
    def test_parse_fenced_json(self):
        self.assertEqual(parse_json_loose('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_loose('前缀 {"b": 2} 后缀'), {"b": 2})
        self.assertIsNone(parse_json_loose("no json here"))


class TestSseFolding(unittest.TestCase):
    """Gateways that answer with an event stream must still work."""

    def _provider(self):
        return OpenAICompatProvider(model="m", base_url="http://x/v1", api_key="k")

    def test_responses_style_stream(self):
        raw = (
            'event: response.created\n'
            'data: {"type":"response.created","response":{"model":"claude-x"}}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"he"}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"llo"}\n\n'
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"model":"claude-x",'
            '"usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}\n\n'
        )
        folded = self._provider()._fold_sse(raw)
        self.assertIsNotNone(folded)
        self.assertEqual(folded["choices"][0]["message"]["content"], "hello")
        self.assertEqual(folded["usage"]["total_tokens"], 15)

    def test_chat_style_stream(self):
        raw = (
            'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"b"},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
            'data: [DONE]\n\n'
        )
        folded = self._provider()._fold_sse(raw)
        self.assertEqual(folded["choices"][0]["message"]["content"], "ab")
        self.assertEqual(folded["usage"]["total_tokens"], 5)

    def test_non_sse_payload_is_not_folded(self):
        self.assertIsNone(self._provider()._fold_sse('{"choices": []}'))

    def test_failed_stream_raises(self):
        from mas.providers import ProviderError

        raw = (
            'data: {"type":"response.failed","response":{"error":{"message":"boom"}}}\n\n'
        )
        with self.assertRaises(ProviderError):
            self._provider()._fold_sse(raw)


class TestStripReasoning(unittest.TestCase):
    def test_removes_think_block(self):
        text = "<think>先想一下</think>\n\n# 交付物\n正文"
        self.assertEqual(strip_reasoning(text), "# 交付物\n正文")

    def test_keeps_plain_text(self):
        self.assertEqual(strip_reasoning("# 交付物"), "# 交付物")


class TestUtf8OutputSafety(unittest.TestCase):
    def test_force_utf8_output_is_idempotent_and_safe(self):
        import io

        class _Legacy:
            """A stream without ``reconfigure`` must not break the CLI."""

            def __init__(self):
                self.text = ""

            def write(self, data):
                self.text += data

            def flush(self):
                pass

        original = sys.stdout
        sys.stdout = _Legacy()
        try:
            force_utf8_output()
            force_utf8_output()
        finally:
            sys.stdout = original

    def test_reconfigure_is_applied_when_available(self):
        buffer = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        original = sys.stdout
        sys.stdout = buffer
        try:
            force_utf8_output()
            self.assertEqual(buffer.encoding, "utf-8")
        finally:
            sys.stdout = original


if __name__ == "__main__":
    unittest.main()


class ConfigLocationTest(unittest.TestCase):
    """The configuration file must not depend on the run-state directory."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="mas-home-")
        self._cfg = os.path.join(self._home, "config.json")
        old = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_CONFIG_PATH"] = self._cfg
        self.addCleanup(shutil.rmtree, self._home, True)
        if old is None:
            self.addCleanup(os.environ.pop, "MAS_CONFIG_PATH", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MAS_CONFIG_PATH", old)

    def test_config_path_follows_the_explicit_override(self):
        from mas.config import user_config_path

        self.assertEqual(user_config_path(), pathlib.Path(self._cfg).resolve())

    def test_a_custom_state_dir_does_not_hide_the_configuration(self):
        state = tempfile.mkdtemp(prefix="mas-state-")
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = state
        config["model"] = "kept-model"
        saved = save_config(config)
        self.assertTrue(saved.exists())
        loaded = load_config()
        self.assertEqual(loaded["model"], "kept-model")
        self.assertTrue(os.path.samefile(loaded["state_dir"], state))

    def test_legacy_state_dir_config_is_still_read(self):
        state = tempfile.mkdtemp(prefix="mas-legacy-")
        legacy = pathlib.Path(state) / "config.json"
        legacy.write_text(json.dumps({"model": "legacy-model"}), encoding="utf-8")
        old_state = os.environ.get("MAS_STATE_DIR")
        os.environ["MAS_STATE_DIR"] = state
        try:
            # Point the well-known location at a file that does not exist, so
            # the v0.1 location is used instead.
            os.environ["MAS_CONFIG_PATH"] = os.path.join(self._home, "missing.json")
            loaded = load_config()
            self.assertEqual(loaded["model"], "legacy-model")
        finally:
            os.environ["MAS_CONFIG_PATH"] = self._cfg
            if old_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = old_state
