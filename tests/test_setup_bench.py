"""Tests for the v0.2 experience layer: wizard, verify and bench."""
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
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mas.bench import run_bench
from mas.config import DEFAULT_CONFIG
from mas.setup import run_verify, run_wizard


class TestWizard(unittest.TestCase):
    """The wizard writes a real config file. Every test here must write into
    a throwaway home, otherwise a test run silently rewrites the user's own
    ~/.mas/config.json."""

    def _answer(self, answers):
        it = iter(answers)
        return lambda *args, **kwargs: next(it)

    def setUp(self):
        # config_path() is derived from state_dir, so MAS_STATE_DIR is the
        # variable that actually isolates the wizard's config file. Using
        # MAS_HOME here would still let the test rewrite ~/.mas/config.json.
        self._home = tempfile.mkdtemp(prefix="mas-wizard-home-")
        self._old_state = os.environ.get("MAS_STATE_DIR")
        self._old_cfg = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_STATE_DIR"] = self._home
        os.environ["MAS_CONFIG_PATH"] = os.path.join(self._home, "config.json")
        self.addCleanup(shutil.rmtree, self._home, True)
        if self._old_state is None:
            self.addCleanup(os.environ.pop, "MAS_STATE_DIR", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MAS_STATE_DIR", self._old_state)
        if self._old_cfg is None:
            self.addCleanup(os.environ.pop, "MAS_CONFIG_PATH", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MAS_CONFIG_PATH", self._old_cfg)

    def test_wizard_writes_a_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The mock backend skips the API-key question, so the answer
            # sequence is: backend, model, base url, team, state dir,
            # workers, reviewers, review mode, max iterations.
            answers = [
                "1",              # mock backend
                "mock-1",         # model
                "",               # base url
                "default",        # team
                tmp,              # state dir
                "3",              # parallel workers
                "3",              # reviewers
                "1",              # review mode -> all
                "5",              # max iterations
                "",               # fallback list: end
            ]
            import builtins

            original = builtins.input
            builtins.input = self._answer(answers)
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    config = run_wizard()
            finally:
                builtins.input = original

            self.assertEqual(config["provider"], "mock")
            self.assertEqual(config["orchestration"]["parallel_workers"], 3)
            self.assertEqual(config["orchestration"]["reviewers"], 3)
            self.assertEqual(config["orchestration"]["review_mode"], "all")
            self.assertEqual(config["budget"]["max_iterations"], 5)
            # The configuration always lands in the well-known home, not
            # next to the ledger - `tmp` only holds the run state.
            self.assertTrue(os.path.exists(os.path.join(self._home, "config.json")))
            self.assertEqual(config["state_dir"], tmp)

    def test_wizard_never_stores_a_plaintext_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = ["1", "mock-1", "", "default", tmp, "1", "1", "1", "3", ""]
            import builtins

            original = builtins.input
            builtins.input = self._answer(answers)
            try:
                with redirect_stdout(io.StringIO()):
                    config = run_wizard()
            finally:
                builtins.input = original
            # The key itself is never written; only the env var name is.
            self.assertEqual(config.get("api_key", ""), "")
            self.assertEqual(config["api_key_env"], "MAS_API_KEY")


class TestVerifyProviderHonesty(unittest.TestCase):
    """``mas verify`` must not rubber-stamp a broken configuration.

    The provider line used to print the configured strings and report
    "ok" no matter what. A beginner with a dead endpoint or a key that
    was never picked up saw a green provider line and no explanation.
    """

    def test_verify_reports_a_dead_endpoint_as_a_failure(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tempfile.mkdtemp()
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        # Nothing listens on port 9.
        config["base_url"] = "http://127.0.0.1:9/v1"
        config["api_key_env"] = "MAS_TEST_DEAD_KEY"
        os.environ["MAS_TEST_DEAD_KEY"] = "k"
        try:
            result = run_verify(config)
        finally:
            os.environ.pop("MAS_TEST_DEAD_KEY", None)
        checks = {row["check"]: row for row in result["checks"]}
        self.assertIn("endpoint", checks)
        self.assertEqual(checks["endpoint"]["status"], "fail")
        # The detail has to be the *real* handshake failure. Without this
        # the test would also pass if the handshake were skipped and some
        # other branch reported "fail" - which is exactly the rubber
        # stamp this check is supposed to eliminate.
        self.assertNotIn("未配置 base_url", checks["endpoint"]["detail"])
        self.assertTrue(
            checks["endpoint"]["detail"].strip(),
            "the endpoint check has to say what actually happened",
        )
        self.assertFalse(result["ok"])

    def test_verify_actually_handshakes_with_the_endpoint(self):
        """The endpoint check has to be a real request, not a string.

        The provider line used to report "ok" for any plausible-looking
        configuration. A test that only asserts the *status* cannot tell
        a real handshake from a hard-coded verdict, so this one counts
        the requests the probe actually made.
        """
        from mas import setup as setup_module

        calls = []
        original = setup_module.probe_from_config

        def counting_probe(config, *args, **kwargs):
            calls.append(dict(config))
            return {"status": "ok", "dialect": "openai-chat-completions",
                    "detail": "端点可用，协议方言 = openai-chat-completions"}

        setup_module.probe_from_config = counting_probe
        try:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tempfile.mkdtemp()
            config["provider"] = "openai_compat"
            config["model"] = "gpt-4o-mini"
            config["base_url"] = "https://example.invalid/v1"
            config["api_key_env"] = "MAS_TEST_HANDSHAKE_KEY"
            os.environ["MAS_TEST_HANDSHAKE_KEY"] = "k"
            try:
                result = run_verify(config)
            finally:
                os.environ.pop("MAS_TEST_HANDSHAKE_KEY", None)
        finally:
            setup_module.probe_from_config = original

        self.assertTrue(calls, "mas verify must talk to the endpoint")
        self.assertEqual(calls[0]["base_url"], "https://example.invalid/v1")
        checks = {row["check"]: row for row in result["checks"]}
        self.assertEqual(checks["endpoint"]["status"], "ok")

    def test_verify_checks_that_the_instruction_reaches_the_model(self):
        """A reachable endpoint that drops the system prompt is not usable.

        On a Responses-style gateway the system instruction used to be
        dropped while the endpoint answered normally - the endpoint
        check said "ok" and every role contract in the loop was
        silently discarded.
        """
        from mas import setup as setup_module

        echoed = []
        original = setup_module.probe_instruction_echo

        def fake_echo(**kwargs):
            echoed.append(kwargs)
            return {"status": "warn",
                    "detail": "端点可用但未采纳系统指令"}

        setup_module.probe_instruction_echo = fake_echo
        try:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tempfile.mkdtemp()
            config["provider"] = "openai_compat"
            config["model"] = "gpt-4o-mini"
            config["base_url"] = "https://example.invalid/v1"
            config["api_key_env"] = "MAS_TEST_ECHO_KEY"
            os.environ["MAS_TEST_ECHO_KEY"] = "secret-value"
            try:
                result = run_verify(config)
            finally:
                os.environ.pop("MAS_TEST_ECHO_KEY", None)
        finally:
            setup_module.probe_instruction_echo = original

        self.assertTrue(echoed, "the instruction check has to run")
        self.assertEqual(echoed[0]["api_key"], "secret-value")
        checks = {row["check"]: row for row in result["checks"]}
        self.assertEqual(checks["instruction"]["status"], "warn")

    def test_verify_says_when_the_key_is_missing(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tempfile.mkdtemp()
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = ""
        config["api_key"] = ""
        config["api_key_env"] = "MAS_TEST_ABSENT_KEY"
        os.environ.pop("MAS_TEST_ABSENT_KEY", None)
        result = run_verify(config)
        checks = {row["check"]: row for row in result["checks"]}
        self.assertIn("未找到密钥", checks["provider"]["detail"])
        self.assertEqual(checks["endpoint"]["status"], "fail")

    def test_verify_says_when_the_key_is_present(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tempfile.mkdtemp()
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = ""
        config["api_key"] = ""
        config["api_key_env"] = "MAS_TEST_PRESENT_KEY"
        os.environ["MAS_TEST_PRESENT_KEY"] = "k"
        try:
            result = run_verify(config)
        finally:
            os.environ.pop("MAS_TEST_PRESENT_KEY", None)
        checks = {row["check"]: row for row in result["checks"]}
        self.assertIn("密钥已就绪", checks["provider"]["detail"])

    def test_mock_mode_never_touches_the_network(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = tempfile.mkdtemp()
        result = run_verify(config)
        names = [row["check"] for row in result["checks"]]
        self.assertNotIn("endpoint", names)
        self.assertNotIn("instruction", names)


class TestApiKeyEnvIsHonoured(unittest.TestCase):
    """The key must come from the environment variable the user named.

    The wizard points the user at ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
    and records that name in the configuration. The provider used to
    read only the *literal* ``api_key`` field, so the documented setup
    produced an empty Authorization header and a 401.
    """

    def test_provider_uses_the_key_from_the_named_env_var(self):
        from mas.providers import build_provider

        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = "https://api.openai.com/v1"
        config["api_key"] = ""
        config["api_key_env"] = "MAS_TEST_ENV_KEY"
        os.environ["MAS_TEST_ENV_KEY"] = "sk-from-env"
        try:
            provider = build_provider(config)
        finally:
            os.environ.pop("MAS_TEST_ENV_KEY", None)
        self.assertEqual(provider.options.get("api_key"), "sk-from-env")

    def test_provider_prefers_a_literal_key_over_the_env_var(self):
        from mas.providers import build_provider

        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = "https://api.openai.com/v1"
        config["api_key"] = "literal-key"
        config["api_key_env"] = "MAS_TEST_ENV_KEY2"
        os.environ["MAS_TEST_ENV_KEY2"] = "sk-from-env"
        try:
            provider = build_provider(config)
        finally:
            os.environ.pop("MAS_TEST_ENV_KEY2", None)
        self.assertEqual(provider.options.get("api_key"), "literal-key")

    def test_a_fallback_does_not_inherit_the_primary_key(self):
        from mas.providers import build_provider_chain

        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = "https://primary.example.com/v1"
        config["api_key"] = "primary-secret"
        config["fallbacks"] = [
            {
                "provider": "openai_compat",
                "model": "backup-model",
                "base_url": "https://backup.example.com/v1",
                "api_key_env": "MAS_TEST_BACKUP_KEY",
            }
        ]
        os.environ["MAS_TEST_BACKUP_KEY"] = "backup-secret"
        try:
            chain = build_provider_chain(config)
        finally:
            os.environ.pop("MAS_TEST_BACKUP_KEY", None)
        backup = chain.providers[1]
        self.assertNotEqual(backup.options.get("api_key"), "primary-secret")
        self.assertEqual(backup.options.get("api_key"), "backup-secret")

    def test_a_fallback_without_its_own_key_keeps_the_primary_one(self):
        from mas.providers import build_provider_chain

        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["provider"] = "openai_compat"
        config["model"] = "gpt-4o-mini"
        config["base_url"] = "https://primary.example.com/v1"
        config["api_key"] = "primary-secret"
        config["fallbacks"] = [
            {
                "provider": "openai_compat",
                "model": "backup-model",
                "base_url": "https://backup.example.com/v1",
            }
        ]
        chain = build_provider_chain(config)
        self.assertEqual(chain.providers[1].options.get("api_key"), "primary-secret")


class TestVerify(unittest.TestCase):
    def test_verify_closes_with_the_mock_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            result = run_verify(config)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["counts"]["fail"], 0)
            names = [row["check"] for row in result["checks"]]
            self.assertIn("closure", names)
            self.assertIn("ledger", names)

    def test_verify_reports_a_failing_backend(self):
        """A broken backend has to be reported, not swallowed.

        The test used to define a ``BrokenProvider`` and never use it:
        it ran the *mock* backend and only checked that a check named
        "closure" exists. So the one thing a beginner relies on after
        a one-click install - "does the self-check notice that my
        backend is dead?" - had no regression protection at all.
        """
        from mas import engine as engine_module
        from mas.providers import ProviderError

        class BrokenProvider:
            name = "broken"
            model = "broken-1"

            def complete(self, **kwargs):
                raise ProviderError("endpoint down")

            def describe(self):
                return {"provider": self.name, "model": self.model}

        original_init = engine_module.LoopEngine.__init__

        def patched_init(self, *args, **kwargs):
            kwargs.pop("provider", None)
            original_init(self, *args, **kwargs)
            self.provider = BrokenProvider()

        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            engine_module.LoopEngine.__init__ = patched_init
            try:
                result = run_verify(config)
            finally:
                engine_module.LoopEngine.__init__ = original_init

            self.assertFalse(result["ok"], "a dead backend must not verify")
            checks = {row["check"]: row for row in result["checks"]}
            self.assertIn("closure", checks)
            self.assertEqual(checks["closure"]["status"], "fail")
            self.assertIn("endpoint down", checks["closure"]["detail"])
            self.assertGreaterEqual(result["counts"]["fail"], 1)

    def test_verify_passes_with_a_healthy_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            result = run_verify(config)
            self.assertTrue(result["ok"], result)
            checks = {row["check"]: row for row in result["checks"]}
            self.assertEqual(checks["closure"]["status"], "ok")

    def test_verify_reports_the_spend_of_the_check_run(self):
        # The self-check spends the user's money. The report has to say
        # how much, or the check is not auditable.
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            result = run_verify(config)
            self.assertIn("token_usage", result)
            self.assertGreaterEqual(result["token_usage"], 0)


class TestBench(unittest.TestCase):
    def test_bench_meets_its_expectations(self):
        result = run_bench(repeats=1)
        self.assertEqual(result["summary"]["runs"], 3)
        self.assertEqual(result["summary"]["expectation_rate"], 1.0)
        self.assertEqual(result["summary"]["closure_rate"], 1.0)
        self.assertEqual(result["summary"]["ledger_integrity_rate"], 1.0)

    def test_bench_is_deterministic(self):
        first = run_bench(repeats=1)
        second = run_bench(repeats=1)
        self.assertEqual(
            [(r["scenario"], r["status"], r["iterations"]) for r in first["rows"]],
            [(r["scenario"], r["status"], r["iterations"]) for r in second["rows"]],
        )

    def test_bench_can_select_a_single_scenario(self):
        result = run_bench(repeats=1, scenarios=["closure"])
        self.assertEqual(result["summary"]["runs"], 1)
        self.assertEqual(result["rows"][0]["scenario"], "closure")


class TestVerifyHandshakeUsesTheRealCredential(unittest.TestCase):
    """``mas verify`` must not diagnose a configuration the user never made.

    The verify handshake has to speak the same dialect as the runtime,
    credentials included. When it dropped the ``api_key_env`` key it
    reported ``401`` against an endpoint that every other request from
    the same configuration authenticated successfully - the beginner is
    told to fix a setup that was already correct.
    """

    def _run_against_authenticated_endpoint(self):
        seen: list = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                seen.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization") != "Bearer verify-secret":
                    body = json.dumps({"error": "missing or wrong api key"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                payload = json.loads(raw)
                blob = json.dumps(payload, ensure_ascii=False)
                system = " ".join(
                    str(m.get("content") or "")
                    for m in (payload.get("messages") or [])
                    if m.get("role") == "system"
                )
                if "MASPROBE7Q" in system:
                    text = "MASPROBE7Q"
                elif "MAS_VERIFY_OK" in blob:
                    text = "用于验证闭环是否真的能跑通的短文，包含 MAS_VERIFY_OK 标记。" * 4
                else:
                    text = "pong"
                body = json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        env_name = "MAS_TEST_VERIFY_KEY"
        old = os.environ.get(env_name)
        os.environ[env_name] = "verify-secret"
        self.addCleanup(
            os.environ.__setitem__, env_name, old
        ) if old is not None else self.addCleanup(os.environ.pop, env_name, None)

        config = {
            "provider": "openai_compat",
            "model": "test-model",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "",
            "api_key_env": env_name,
            "timeout_sec": 15,
            "team": "default",
            "budget": {"max_iterations": 2, "deadline_sec": 60},
        }
        result = run_verify(config)
        return result, seen

    def test_verify_reports_the_endpoint_as_reachable(self):
        result, seen = self._run_against_authenticated_endpoint()
        rows = {row["check"]: row for row in result["checks"]}
        self.assertEqual(rows["endpoint"]["status"], "ok", rows["endpoint"])
        self.assertTrue(seen, "verify must talk to the endpoint")
        self.assertTrue(
            all(auth == "Bearer verify-secret" for auth in seen),
            f"every verify request must carry the configured key, saw: {seen}",
        )

    def test_verify_closes_the_loop_against_an_authenticated_endpoint(self):
        result, _ = self._run_against_authenticated_endpoint()
        rows = {row["check"]: row for row in result["checks"]}
        self.assertEqual(rows["closure"]["status"], "ok", rows["closure"])
        self.assertTrue(result["ok"], result["checks"])


if __name__ == "__main__":
    unittest.main()


class WizardFallbackTest(unittest.TestCase):
    """The wizard must be able to record a failover chain."""

    def test_wizard_records_fallbacks(self):
        import mas.setup as setup

        home = tempfile.mkdtemp(prefix="mas-wizard-fallback-")
        answers = iter([
            "1",            # backend: mock
            "mock-1",       # model
            "",             # base url
            "default",      # team
            home,           # state dir
            "2",            # parallel workers
            "2",            # reviewers
            "2",            # review mode: majority
            "4",            # max iterations
            "openai_compat,https://backup.example.com/v1,kimi-k2",
            "",             # end of fallback list
        ])
        old_state = os.environ.get("MAS_STATE_DIR")
        old_cfg = os.environ.get("MAS_CONFIG_PATH")
        os.environ["MAS_STATE_DIR"] = home
        os.environ["MAS_CONFIG_PATH"] = os.path.join(home, "config.json")
        original = setup._ask
        setup._ask = lambda prompt, default="": (next(answers) or default)
        try:
            config = setup.run_wizard()
        finally:
            setup._ask = original
            shutil.rmtree(home, ignore_errors=True)
            if old_state is None:
                os.environ.pop("MAS_STATE_DIR", None)
            else:
                os.environ["MAS_STATE_DIR"] = old_state
            if old_cfg is None:
                os.environ.pop("MAS_CONFIG_PATH", None)
            else:
                os.environ["MAS_CONFIG_PATH"] = old_cfg
        self.assertEqual(config["orchestration"]["parallel_workers"], 2)
        self.assertEqual(config["orchestration"]["review_mode"], "majority")
        self.assertEqual(len(config["fallbacks"]), 1)
        self.assertEqual(config["fallbacks"][0]["model"], "kimi-k2")
