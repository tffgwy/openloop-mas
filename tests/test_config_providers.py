"""Config and provider tests."""

import io
import json
import sys
import os
import pathlib
import shutil
import tempfile
import unittest

from mas.config import (DEFAULT_CONFIG, DEFAULT_STATE_DIR, load_config, load_persisted_config,
                       persistable_config, resolve_api_key, save_config)
from mas.providers import MockProvider, OpenAICompatProvider, build_provider
from mas.util import force_utf8_output, parse_json_loose, strip_reasoning


class TestConfig(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            # Write to an isolated path. Writing to the well-known location
            # would overwrite the real user configuration with a temporary
            # state directory.
            path = save_config(config, os.path.join(tmp, "config.json"))
            self.assertTrue(path.exists())
            loaded = load_config(path)
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
        # Read the file as it is on disk, without the environment override.
        persisted = load_persisted_config(saved)
        self.assertEqual(persisted["model"], "kept-model")
        self.assertTrue(os.path.samefile(persisted["state_dir"], state))
        # With the environment in play, the environment wins - that is the
        # documented precedence.
        loaded = load_config(saved)
        self.assertEqual(loaded["model"], "kept-model")

    def test_an_explicit_config_path_is_authoritative(self):
        """An existing explicit path must win over every other location.

        When MAS_CONFIG_PATH names a file that exists, the loader must read
        exactly that file. Otherwise an isolated run could pick up
        somebody else's settings - in the test-suite it reached the
        developer's real configuration and started making live network
        calls.
        """
        explicit = os.path.join(self._home, "explicit.json")
        with open(explicit, "w", encoding="utf-8") as handle:
            json.dump({"model": "explicit-model"}, handle)
        os.environ["MAS_CONFIG_PATH"] = explicit
        try:
            loaded = load_config()
            self.assertEqual(loaded["model"], "explicit-model")
        finally:
            os.environ["MAS_CONFIG_PATH"] = self._cfg

    def test_legacy_state_dir_config_is_still_read(self):
        """The v0.1 layout - config next to the state directory - still works.

        This path is only taken when no explicit MAS_CONFIG_PATH is set.
        """
        state = tempfile.mkdtemp(prefix="mas-legacy-")
        legacy = pathlib.Path(state) / "config.json"
        legacy.write_text(json.dumps({"model": "legacy-model"}), encoding="utf-8")
        old_state = os.environ.get("MAS_STATE_DIR")
        old_cfg = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_STATE_DIR"] = state
        # The well-known location must not exist, otherwise it wins over
        # the legacy one. MAS_CONFIG_PATH is the well-known location when
        # it is set, so point it at a file that is not there.
        os.environ["MAS_CONFIG_PATH"] = os.path.join(self._home, "missing.json")
        try:
            loaded = load_config()
            self.assertEqual(loaded["model"], "legacy-model")
        finally:
            if old_cfg is not None:
                os.environ["MAS_CONFIG_PATH"] = old_cfg
            else:
                os.environ.pop("MAS_CONFIG_PATH", None)
            if old_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = old_state


class ConfigPersistenceHygieneTest(unittest.TestCase):
    """The environment must stay authoritative and must not be frozen to disk."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="mas-home-")
        self._cfg = os.path.join(self._home, "config.json")
        self._saved_env = {}
        for name in ("MAS_CONFIG_PATH", "MAS_STATE_DIR", "MAS_API_KEY", "MAS_PROVIDER", "MAS_MODEL"):
            self._saved_env[name] = os.environ.get(name)
            os.environ.pop(name, None)
        os.environ["MAS_CONFIG_PATH"] = self._cfg
        self.addCleanup(shutil.rmtree, self._home, True)
        for name, value in self._saved_env.items():
            def restore(name=name, value=value):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            self.addCleanup(restore)

    def test_env_state_dir_is_not_persisted(self):
        os.environ["MAS_STATE_DIR"] = tempfile.mkdtemp(prefix="mas-env-state-")
        try:
            config = load_config()
            self.assertEqual(config["state_dir"], os.environ["MAS_STATE_DIR"])
            save_config(config)
            on_disk = json.loads(pathlib.Path(self._cfg).read_text(encoding="utf-8"))
            self.assertNotEqual(on_disk["state_dir"], os.environ["MAS_STATE_DIR"])
        finally:
            os.environ.pop("MAS_STATE_DIR", None)

    def test_env_api_key_is_not_persisted(self):
        os.environ["MAS_API_KEY"] = "sk-super-secret-value"
        try:
            config = load_config()
            self.assertEqual(config["api_key"], "sk-super-secret-value")
            save_config(config)
            raw = pathlib.Path(self._cfg).read_text(encoding="utf-8")
            self.assertNotIn("sk-super-secret-value", raw)
            self.assertEqual(json.loads(raw)["api_key"], "")
            self.assertEqual(json.loads(raw)["api_key_env"], "MAS_API_KEY")
        finally:
            os.environ.pop("MAS_API_KEY", None)

    def test_persisted_view_ignores_the_environment(self):
        # Use an isolated file so the developer's real configuration is
        # never part of the assertion.
        isolated = os.path.join(self._home, "isolated.json")
        with open(isolated, "w", encoding="utf-8") as handle:
            json.dump({"provider": "mock", "model": "file-model"}, handle)
        os.environ["MAS_PROVIDER"] = "openai_compat"
        os.environ["MAS_MODEL"] = "some-model"
        try:
            persisted = load_persisted_config(isolated)
            self.assertEqual(persisted["provider"], "mock")
            self.assertEqual(persisted["model"], "file-model")
            effective = load_config(isolated)
            self.assertEqual(effective["provider"], "openai_compat")
            self.assertEqual(effective["model"], "some-model")
        finally:
            os.environ.pop("MAS_PROVIDER", None)
            os.environ.pop("MAS_MODEL", None)

    def test_persistable_config_keeps_the_env_variable_name(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["api_key_env"] = "MY_KEY_VAR"
        payload = persistable_config(config)
        self.assertEqual(payload["api_key_env"], "MY_KEY_VAR")
        self.assertEqual(payload["api_key"], "")

    def test_ephemeral_state_dir_is_not_persisted(self):
        # The exact shape tempfile.mkdtemp() produces - this is what an
        # injected MAS_STATE_DIR or a stray test run leaves behind.
        import tempfile as _tf
        tmp_state = _tf.mkdtemp(prefix="tmp")
        self.assertTrue(os.path.basename(tmp_state).startswith("tmp"))
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tmp_state
        payload = persistable_config(config)
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_a_user_named_temp_dir_is_kept(self):
        import tempfile as _tf
        tmp_state = _tf.mkdtemp(prefix="mas-ledger-")
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tmp_state
        payload = persistable_config(config)
        self.assertEqual(payload["state_dir"], tmp_state)

    def test_a_real_state_dir_is_kept(self):
        state = os.path.join(tempfile.gettempdir(), "..", "mas-real-state-demo")
        state = os.path.abspath(state)
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = state
        payload = persistable_config(config)
        self.assertEqual(payload["state_dir"], state)

    def test_resolve_api_key_still_reads_the_environment(self):
        os.environ["MY_KEY_VAR"] = "value-from-env"
        try:
            config = {"api_key_env": "MY_KEY_VAR", "api_key": ""}
            self.assertEqual(resolve_api_key(config), "value-from-env")
        finally:
            os.environ.pop("MY_KEY_VAR", None)


class VerdictNormalizationTest(unittest.TestCase):
    """A reviewer that answers with its own JSON shape must not be lost."""

    def test_canonical_shape_is_preserved(self):
        from mas.util import normalize_verdict

        payload = {
            "verdict": "pass",
            "scores": [{"id": "c1", "pass": True, "note": "ok"}],
        }
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "pass")
        self.assertEqual(len(out["scores"]), 1)
        self.assertTrue(out["scores"][0]["pass"])

    def test_criteria_shape_is_converted(self):
        from mas.util import normalize_verdict

        payload = {
            "overall_pass": False,
            "criteria": [
                {"id": "c1", "result": "FAIL", "reason": "缺少遍历"},
                {"id": "c2", "result": "PASS", "reason": "说明完整"},
            ],
        }
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(len(out["scores"]), 2)
        self.assertFalse(out["scores"][0]["pass"])
        self.assertTrue(out["scores"][1]["pass"])
        self.assertEqual(out["scores"][0]["id"], "c1")

    def test_boolean_field_names_are_understood(self):
        from mas.util import normalize_verdict

        payload = {"results": [{"criterion": "c1", "passed": True, "explanation": "fine"}]}
        out = normalize_verdict(payload)
        self.assertEqual(out["scores"][0]["id"], "c1")
        self.assertTrue(out["scores"][0]["pass"])
        self.assertEqual(out["verdict"], "pass")

    def test_unrelated_payload_is_left_alone(self):
        from mas.util import normalize_verdict

        payload = {"summary": "nothing to score"}
        out = normalize_verdict(payload)
        self.assertEqual(out, payload)


class ProviderPayloadTest(unittest.TestCase):
    """The request must carry the prompt in a field the gateway reads."""

    def test_input_field_is_sent_alongside_messages(self):
        provider = OpenAICompatProvider(model="m", base_url="http://x/v1", api_key="k")
        captured = {}

        def fake_post(url, payload, headers):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 1}}

        provider._post_json = fake_post
        provider.complete(system="s", prompt="the task", role="worker")
        self.assertIn("messages", captured["payload"])
        self.assertEqual(captured["payload"]["input"], "the task")
        self.assertEqual(captured["payload"]["messages"][1]["content"], "the task")

    def test_fallback_can_override_the_connection_settings(self):
        from mas.providers import build_provider_chain

        config = {
            "provider": "openai_compat",
            "model": "primary",
            "base_url": "http://primary.invalid/v1",
            "timeout_sec": 3,
            "max_retries": 0,
            "fallbacks": [
                {
                    "provider": "openai_compat",
                    "model": "backup",
                    "base_url": "http://backup.invalid/v1",
                    "timeout_sec": 90,
                    "max_retries": 4,
                }
            ],
        }
        chain = build_provider_chain(config)
        self.assertEqual(len(chain.providers), 2)
        backup = chain.providers[1]
        self.assertEqual(backup.model, "backup")
        self.assertEqual(backup.options["timeout_sec"], 90)
        self.assertEqual(backup.options["max_retries"], 4)
        self.assertEqual(backup.options["base_url"], "http://backup.invalid/v1")
