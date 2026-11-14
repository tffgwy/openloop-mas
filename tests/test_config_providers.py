"""Config and provider tests."""
# The session-wide test isolation must be active before this module is
# imported: importing a test module is what pulls in the code under test.
# ``unittest discover -s tests`` loads modules as top-level ``test_x`` and
# therefore never executes ``tests/__init__.py``, so the isolation cannot
# rely on the package hook alone - every test module imports it directly.
try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

from mas.config import (
    DEFAULT_CONFIG,
    DEFAULT_STATE_DIR,
    load_config,
    load_persisted_config,
    persistable_config,
    resolve_api_key,
    save_config,
)
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
        saved = {
            name: os.environ.get(name)
            for name in ("MAS_PROVIDER", "MAS_MODEL")
        }
        os.environ["MAS_PROVIDER"] = "openai_compat"
        os.environ["MAS_MODEL"] = "kimi-k2"
        try:
            loaded = load_config()
            self.assertEqual(loaded["provider"], "openai_compat")
            self.assertEqual(loaded["model"], "kimi-k2")
        finally:
            # Restore, never pop: popping would silently remove the
            # session-wide isolation that the rest of the suite relies on.
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_partial_file_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"provider": "anthropic"}, handle)
            # The persisted view is what the file actually says: a partial
            # file must not lose the defaults it does not mention.
            persisted = load_persisted_config(path)
            self.assertEqual(persisted["provider"], "anthropic")
            self.assertEqual(persisted["budget"]["max_iterations"], DEFAULT_CONFIG["budget"]["max_iterations"])
            # The effective view additionally applies the environment, which
            # by documented design wins over the file.
            effective = load_config(path)
            self.assertEqual(effective["provider"], os.environ.get("MAS_PROVIDER") or "anthropic")


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

    def test_mock_verifier_reports_each_criterion_once(self):
        # The mock verifier used to report a criterion without a machine
        # check twice: once from the check list (pass: False, note "no
        # machine check") and once from the unjudged list. The first entry
        # won, so the useful "mock cannot judge this" note was lost.
        provider = MockProvider()
        state = {
            "attempt": 1,
            "goal": "g",
            "criteria": [
                {"id": "c1", "desc": "d", "check": {"type": "min_chars", "arg": 1}},
                {"id": "c2", "desc": "d2", "check": None},
            ],
            "checks": [
                {"id": "c1", "pass": True, "note": "length 5 >= 1"},
                {"id": "c2", "pass": None, "note": "no machine check"},
            ],
        }
        prompt = (
            "<<MAS_STATE>>\n"
            + json.dumps(state, ensure_ascii=False)
            + "\n<<END>>"
        )
        result = provider.complete(system="s", prompt=prompt, role="verifier")
        payload = json.loads(result["text"])
        ids = [sc["id"] for sc in payload["scores"]]
        self.assertEqual(sorted(ids), ["c1", "c2"])
        self.assertEqual(len(ids), len(set(ids)))
        c2 = [sc for sc in payload["scores"] if sc["id"] == "c2"][0]
        self.assertIn("mock", c2["note"])

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


class TestTruncationDetection(unittest.TestCase):
    """A cut off answer must never be delivered as if it were complete."""

    def test_finish_reason_length_is_truncated(self):
        from mas.providers import is_truncated
        self.assertTrue(is_truncated({"choices": [{"finish_reason": "length"}]}))

    def test_finish_reason_stop_is_not_truncated(self):
        from mas.providers import is_truncated
        self.assertFalse(is_truncated({"choices": [{"finish_reason": "stop"}]}))

    def test_the_anthropic_stop_reason_is_truncated(self):
        from mas.providers import is_truncated
        self.assertTrue(is_truncated({"stop_reason": "max_tokens"}))

    def test_a_missing_finish_reason_is_not_truncated(self):
        from mas.providers import is_truncated
        self.assertFalse(is_truncated({"choices": [{}]}))

    def test_an_empty_answer_is_not_truncated(self):
        from mas.providers import is_truncated
        self.assertFalse(is_truncated({}))

    def test_the_openai_provider_raises_on_a_truncated_answer(self):
        from mas.providers import OpenAICompatProvider, TruncatedAnswer

        provider = OpenAICompatProvider(model="m", base_url="http://x/v1", api_key="k")
        data = {
            "choices": [{"message": {"content": "half an answer"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        with self.assertRaises(TruncatedAnswer):
            provider._post_json = lambda *a, **k: data
            provider.complete(system="s", prompt="p", role="worker")

    def test_the_openai_provider_returns_a_complete_answer(self):
        from mas.providers import OpenAICompatProvider

        provider = OpenAICompatProvider(model="m", base_url="http://x/v1", api_key="k")
        data = {
            "choices": [{"message": {"content": "the answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        provider._post_json = lambda *a, **k: data
        result = provider.complete(system="s", prompt="p", role="worker")
        self.assertEqual(result["text"], "the answer")

    def test_the_anthropic_provider_raises_on_a_truncated_answer(self):
        from mas.providers import AnthropicProvider, TruncatedAnswer

        provider = AnthropicProvider(model="m", base_url="http://x", api_key="k")
        data = {
            "content": [{"type": "text", "text": "half an answer"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }
        with self.assertRaises(TruncatedAnswer):
            provider._post_json = lambda *a, **k: data
            provider.complete(system="s", prompt="p", role="worker")


class TestStripReasoning(unittest.TestCase):
    def test_removes_think_block(self):
        text = "<think>先想一下</think>\n\n# 交付物\n正文"
        self.assertEqual(strip_reasoning(text), "# 交付物\n正文")

    def test_keeps_plain_text(self):
        self.assertEqual(strip_reasoning("# 交付物"), "# 交付物")

    def test_removes_an_unclosed_think_block(self):
        # The model ran out of tokens in the middle of its scratchpad, so
        # the closing tag never arrives. Everything from the opening tag
        # on is scratchpad - it must not reach the deliverable.
        text = "<think>先想一下，然后还没想完就"
        self.assertEqual(strip_reasoning(text), "")

    def test_an_unclosed_block_does_not_eat_a_later_closed_one(self):
        # Two scratchpads: the first one unclosed because the model was
        # cut off, the second one complete. The whole text is scratchpad.
        text = "<think>first</think><think>second</think>"
        self.assertEqual(strip_reasoning(text), "")

    def test_removes_the_thinking_spelling(self):
        self.assertEqual(strip_reasoning("<thinking>x</thinking>正文"), "正文")

    def test_removes_the_reasoning_spelling(self):
        # Other gateways use a different tag for the same scratchpad.
        self.assertEqual(strip_reasoning("<reasoning>x</reasoning>正文"), "正文")

    def test_removes_the_scratchpad_spelling(self):
        self.assertEqual(strip_reasoning("<scratchpad>x</scratchpad>正文"), "正文")

    def test_removes_an_unclosed_reasoning_block(self):
        self.assertEqual(strip_reasoning("<reasoning>还在想"), "")

    def test_a_closed_block_is_not_treated_as_an_opening_one(self):
        # The unclosed rule must not fire on a *closed* block: the text
        # after it is the deliverable and has to survive.
        text = "<think>x</think>正文"
        self.assertEqual(strip_reasoning(text), "正文")

    def test_the_real_answer_after_a_closed_block_survives(self):
        text = "<think>x</think>\n\n# 交付物\n正文"
        self.assertEqual(strip_reasoning(text), "# 交付物\n正文")


class TestUtf8OutputSafety(unittest.TestCase):
    def test_force_utf8_output_is_idempotent_and_safe(self):

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
        saved = {
            name: os.environ.get(name)
            for name in ("MAS_PROVIDER", "MAS_MODEL")
        }
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
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

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

    def test_an_unreadable_verdict_never_produces_a_pass(self):
        # The canonical path left the raw string in place, so the
        # aggregate read "maybe" as truthy and derived verdict: pass.
        from mas.util import normalize_verdict

        payload = {"verdict": "", "scores": [{"id": "c1", "pass": "maybe", "note": "x"}]}
        out = normalize_verdict(payload)
        self.assertIs(out["scores"][0]["pass"], False)
        self.assertTrue(out["scores"][0].get("unreadable"))
        self.assertEqual(out["verdict"], "fail")

    def test_both_verdict_shapes_agree_on_an_unreadable_answer(self):
        from mas.util import normalize_verdict

        canonical = normalize_verdict(
            {"verdict": "", "scores": [{"id": "c1", "pass": "maybe"}]}
        )
        alternative = normalize_verdict(
            {"criteria": [{"id": "c1", "pass": "maybe"}]}
        )
        self.assertEqual(canonical["verdict"], alternative["verdict"])
        self.assertEqual(canonical["scores"][0]["pass"], alternative["scores"][0]["pass"])

    def test_a_pass_summary_cannot_contradict_a_failed_score(self):
        # The model answered "pass" in the summary while its own
        # per-criterion answer said "no". The closure is computed from
        # the scores, but every report showed the contradicting summary.
        from mas.util import normalize_verdict

        payload = {"verdict": "pass", "scores": [{"id": "c1", "pass": "false", "note": "no"}]}
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "fail")
        self.assertIs(out["scores"][0]["pass"], False)

    def test_a_fail_summary_cannot_contradict_a_passing_score(self):
        from mas.util import normalize_verdict

        payload = {"verdict": "fail", "scores": [{"id": "c1", "pass": True, "note": "yes"}]}
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "pass")

    def test_a_mixed_payload_summarises_as_fail(self):
        from mas.util import normalize_verdict

        payload = {
            "verdict": "pass",
            "scores": [{"id": "c1", "pass": True}, {"id": "c2", "pass": False}],
        }
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "fail")

    def test_the_summary_survives_when_no_score_is_readable(self):
        # With nothing readable in the scores, the model's summary is
        # the only signal there is - it must not be thrown away.
        from mas.util import normalize_verdict

        payload = {"verdict": "pass", "scores": [{"id": "c1", "pass": "maybe"}]}
        out = normalize_verdict(payload)
        self.assertEqual(out["verdict"], "pass")
        self.assertTrue(out["scores"][0].get("unreadable"))

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
        self.assertEqual(captured["payload"]["input"][-1]["content"], "the task")
        self.assertEqual(captured["payload"]["messages"][1]["content"], "the task")

    def test_input_carries_the_system_instruction_too(self):
        """A Responses-style gateway reads ``input`` and ignores ``messages``.

        Sending the bare prompt in ``input`` silently dropped the system
        instruction on those gateways, so every role contract stopped
        applying. ``input`` must carry the system item as well.
        """
        provider = OpenAICompatProvider(model="m", base_url="http://x/v1", api_key="k")
        captured = {}

        def fake_post(url, payload, headers):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 1}}

        provider._post_json = fake_post
        provider.complete(system="ROLE-CONTRACT", prompt="the task", role="worker")
        items = captured["payload"]["input"]
        self.assertEqual([item["role"] for item in items], ["system", "user"])
        self.assertEqual(items[0]["content"], "ROLE-CONTRACT")

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


class CriterionShorthandTest(unittest.TestCase):
    """The ``<type>:<arg>`` shorthand must produce a real machine check.

    Before v0.4.2 the shorthand was passed through as a plain description,
    so the machine check silently never ran and the criterion was decided by
    the model instead of the rule. That made a "regex criterion" look like
    it passed while actually being a model judgement.
    """

    def test_regex_shorthand_becomes_a_machine_check(self):
        from mas.cli import _parse_criterion
        from mas.engine import Criterion, evaluate_check

        parsed = _parse_criterion("regex:def dedupe")
        criterion = Criterion.from_raw(parsed, 0)
        self.assertIsNotNone(criterion.check)
        self.assertEqual(criterion.check["type"], "regex")
        self.assertEqual(criterion.check["arg"], "def dedupe")

        outcome = evaluate_check(criterion, "def dedupe(items):\n    return items")
        self.assertTrue(outcome["pass"])
        self.assertEqual(outcome["source"], "rule")

    def test_the_shorthand_works_on_the_engine_path_too(self):
        """The MCP and HTTP servers pass criteria straight to the engine."""
        from mas.engine import Criterion

        criterion = Criterion.from_raw("regex:hello", 0)
        self.assertEqual(criterion.check, {"type": "regex", "arg": "hello"})

    def test_numeric_shorthands_are_converted_to_integers(self):
        from mas.cli import _parse_criterion

        parsed = _parse_criterion("min_chars:200")
        self.assertEqual(parsed["check"]["arg"], 200)

    def test_a_regex_may_contain_colons(self):
        from mas.cli import _parse_criterion

        parsed = _parse_criterion("regex:https?://example")
        self.assertEqual(parsed["check"]["arg"], "https?://example")

    def test_unknown_types_stay_plain_descriptions(self):
        from mas.cli import _parse_criterion

        self.assertEqual(_parse_criterion("写清楚就好"), "写清楚就好")
        self.assertEqual(_parse_criterion("unknown_type:abc"), "unknown_type:abc")

    def test_a_non_numeric_numeric_shorthand_keeps_its_check(self):
        """A broken argument must not silently become a description.

        Downgrading "min_chars:many" to a plain description handed the
        criterion to the model, so the standard was never machine-checked
        and the loop still burned its whole budget. The check has to
        survive parsing so the engine can refuse the run up front.
        """
        from mas.cli import _parse_criterion

        parsed = _parse_criterion("min_chars:many")
        self.assertEqual(parsed["check"]["type"], "min_chars")
        self.assertEqual(parsed["check"]["arg"], "many")

    def test_an_empty_argument_keeps_its_check(self):
        from mas.cli import _parse_criterion

        parsed = _parse_criterion("regex:")
        self.assertEqual(parsed["check"]["type"], "regex")
        self.assertEqual(parsed["check"]["arg"], "")


class CriterionJsonArrayTest(unittest.TestCase):
    """A JSON *array* of criteria must not degrade into one description.

    ``--criteria '[{...},{...}]'`` was rejected as "not a shorthand" and
    kept as a plain text description, so every machine check in the array
    silently never ran. The run then closed on a *model* judgement and
    the report claimed the machine-verified standards had passed.
    """

    def test_a_json_array_is_parsed_into_a_list(self):
        from mas.cli import _parse_criterion

        parsed = _parse_criterion('[{"id":"c1","desc":"a"},{"id":"c2","desc":"b"}]')
        self.assertIsInstance(parsed, list)
        self.assertEqual([c["id"] for c in parsed], ["c1", "c2"])

    def test_a_json_array_keeps_the_machine_checks(self):
        from mas.cli import _parse_criterion
        from mas.engine import Criterion, evaluate_check

        parsed = _parse_criterion(
            '[{"id":"c1","desc":"has def","check":{"type":"contains","arg":"def dedupe"}}]'
        )
        criterion = Criterion.from_raw(parsed[0], 0)
        self.assertEqual(criterion.check["type"], "contains")
        outcome = evaluate_check(criterion, "def dedupe(items):\n    return items")
        self.assertTrue(outcome["pass"])
        self.assertEqual(outcome["source"], "rule")

    def test_the_cli_flattens_a_json_array_into_the_criteria_list(self):
        import argparse

        from mas.cli import _load_criteria

        args = argparse.Namespace(
            criteria_file=None,
            criteria=['[{"id":"c1","desc":"a"},{"id":"c2","desc":"b"}]', "regex:hello"],
        )
        criteria = _load_criteria(args)
        self.assertEqual(len(criteria), 3)
        self.assertEqual(criteria[0]["id"], "c1")
        self.assertEqual(criteria[1]["id"], "c2")
        self.assertEqual(criteria[2]["check"]["type"], "regex")

    def test_a_json_array_with_a_soft_requirement_stays_soft(self):
        from mas.cli import _parse_criterion
        from mas.engine import Criterion

        parsed = _parse_criterion(
            '[{"id":"c1","desc":"hard","check":{"type":"contains","arg":"x"}},'
            '{"id":"c2","desc":"soft","required":"false",'
            '"check":{"type":"min_chars","arg":999999}}]'
        )
        self.assertTrue(Criterion.from_raw(parsed[0], 0).required)
        self.assertFalse(Criterion.from_raw(parsed[1], 1).required)

    def test_a_broken_json_array_is_refused(self):
        from mas.cli import _parse_criterion

        with self.assertRaises(ValueError):
            _parse_criterion('[{"id":"c1",}')


class DegenerateShorthandTest(unittest.TestCase):
    """A broken shorthand must be refused before the loop spends tokens.

    v0.7.4 made a *JSON* criterion with a missing argument fail fast, but
    the shorthand form still degraded to a plain description. The same
    standard written two ways therefore behaved two ways: the JSON form
    exited immediately, the shorthand form ran the whole budget and then
    escalated to a human ticket.
    """

    def _criterion(self, text):
        from mas.engine import Criterion

        return Criterion.from_raw(text, 0)

    def test_an_empty_tail_keeps_its_check_type(self):
        for text in ("regex:", "contains:", "section:", "json_keys:",
                     "min_chars:", "max_chars:", "lines_min:", "not_contains:"):
            with self.subTest(text=text):
                criterion = self._criterion(text)
                self.assertIsNotNone(criterion.check)
                self.assertEqual(criterion.check["type"], text.split(":")[0])

    def test_a_non_numeric_count_keeps_its_check(self):
        criterion = self._criterion("min_chars:many")
        self.assertIsNotNone(criterion.check)
        self.assertEqual(criterion.check["type"], "min_chars")

    def test_the_engine_refuses_the_run(self):
        from mas.engine import validate_criteria

        problems = validate_criteria([self._criterion("regex:")])
        self.assertTrue(problems)
        self.assertIn("c1", problems[0])

    def test_a_broken_shorthand_never_passes_a_check(self):
        from mas.engine import evaluate_check

        outcome = evaluate_check(self._criterion("regex:"), "anything")
        self.assertIsNone(outcome["pass"])
        self.assertEqual(outcome["source"], "invalid")

    def test_an_unknown_type_is_still_only_a_description(self):
        criterion = self._criterion("unknown_type:abc")
        self.assertIsNone(criterion.check)

    def test_a_valid_shorthand_is_unaffected(self):
        criterion = self._criterion("regex:def dedupe")
        self.assertEqual(criterion.check, {"type": "regex", "arg": "def dedupe"})


class MissingVerdictRecoveryTest(unittest.TestCase):
    """A silent verifier must not burn a whole iteration.

    When the verifier returns no verdict for a criterion, the artifact is
    not necessarily wrong - the answer is missing. The engine asks the
    verifier again for the missing ids instead of regenerating the
    artifact and starting a new attempt.
    """

    class _SilentThenAnsweringProvider:
        """Answers the first verifier call without any score entries."""

        name = "silent-then-answering"
        model = "stub-1"

        def __init__(self):
            self.calls = 0
            self.verifier_calls = 0

        def complete(self, *, system, prompt, role="worker", expect_json=False, max_tokens=4000):
            self.calls += 1
            if role == "planner":
                text = '{"summary": "s", "plan": ["p"], "risks": []}'
            elif role == "reflector":
                text = '{"summary": "r", "issues": [], "next_action": "revise"}'
            elif role == "verifier":
                self.verifier_calls += 1
                if self.verifier_calls == 1:
                    # Silent: a verdict without any scores.
                    text = '{"verdict": "pass", "scores": [], "issues": []}'
                else:
                    text = json.dumps(
                        {
                            "verdict": "pass",
                            "scores": [
                                {"id": "c1", "pass": True, "note": "解释清楚了"}
                            ],
                            "issues": [],
                        },
                        ensure_ascii=False,
                    )
            else:
                text = "解释幂等性：同一个操作执行一次和多次，结果相同。"
            return {"text": text, "usage": {"total_tokens": 10}, "raw": {}}

        def describe(self):
            return {"chain": []}

    def _engine(self):
        from mas.engine import LoopEngine

        provider = self._SilentThenAnsweringProvider()
        config = {
            "state_dir": tempfile.mkdtemp(prefix="mas-missing-verdict-"),
            "budget": {"max_iterations": 8, "deadline_sec": 60, "max_tokens": 100000},
            "stagnation_limit": 3,
            "orchestration": {"parallel_workers": 1, "reviewers": 1, "review_mode": "all"},
        }
        return LoopEngine(config, provider=provider), provider

    def test_the_loop_closes_in_one_iteration(self):
        engine, provider = self._engine()
        result = engine.run(
            goal="解释幂等性",
            criteria=["说明中解释清楚幂等性"],
        )
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.iterations, 1)
        self.assertEqual(provider.verifier_calls, 2)

    def test_the_recovered_verdict_is_recorded(self):
        engine, _provider = self._engine()
        result = engine.run(
            goal="解释幂等性",
            criteria=["说明中解释清楚幂等性"],
        )
        score = [s for s in result.scores if s["id"] == "c1"][0]
        self.assertTrue(score["pass"])
        self.assertEqual(score["source"], "model")
