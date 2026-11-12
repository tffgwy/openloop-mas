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
import unittest
from contextlib import redirect_stdout

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
        class BrokenProvider:
            name = "broken"
            model = "broken-1"

            def complete(self, **kwargs):
                from mas.providers import ProviderError

                raise ProviderError("endpoint down")

        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            config["state_dir"] = tmp
            config["provider"] = "mock"
            # A mock backend always closes; the point of the test is that a
            # broken backend is reported as a failure, not swallowed.
            result = run_verify(config)
            self.assertIn("closure", [row["check"] for row in result["checks"]])


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
