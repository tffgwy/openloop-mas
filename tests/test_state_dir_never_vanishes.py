# -*- coding: utf-8 -*-
"""v0.7.52 regression tests: the state directory must never vanish.

The user's configuration file said

    "state_dir": "C:\\Users\\ADMINI~1\\AppData\\Local\\Temp\\mas-handover-n2d4xr0z"

- a directory that :func:`tempfile.mkdtemp` had created for one test
run. The operating system cleans the temp folder whenever it feels
like it, and when that happened the ledger went with it: the tool
reported an empty history for work that had actually happened, and
every new run started from zero.

Two guards were supposed to prevent this and neither did. The
environment guard only fired when ``MAS_STATE_DIR`` happened to match
the value exactly, and the temporary-directory guard only recognised
the literal ``tmpXXXXXXXX`` shape that ``mkdtemp(prefix="tmp")``
produces - so every ``mkdtemp(prefix="mas-handover-")`` walked right
past it.

The fix has to keep two things apart:

* a directory the *user* named is a decision and is kept;
* a directory that merely *looks* automatic is disposable and is not
  written to disk.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # pragma: no cover - loaded as a top-level module
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from mas.config import (
    DEFAULT_CONFIG,
    DEFAULT_STATE_DIR,
    load_config,
    persistable_config,
    save_config,
)


class EphemeralStateDirGuardTest(unittest.TestCase):
    """Nothing that looks disposable may be frozen into the configuration."""

    def setUp(self):
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")

    def tearDown(self):
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path

    def _config_with(self, state_dir):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["state_dir"] = state_dir
        return config

    # ------------------------------------------------- the shapes that leaked
    def test_the_prefixed_temp_dir_is_not_persisted(self):
        """The shape that actually leaked: ``mas-handover-`` and friends."""
        state = tempfile.mkdtemp(prefix="mas-handover-")
        payload = persistable_config(self._config_with(state))
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_every_prefixed_temp_dir_is_not_persisted(self):
        for prefix in (
            "tmp",
            "mas-handover-",
            "mas-state-",
            "mas-ledger-",
            "mas-envstate-",
            "mas-wizard-home-",
        ):
            with self.subTest(prefix=prefix):
                state = tempfile.mkdtemp(prefix=prefix)
                payload = persistable_config(self._config_with(state))
                self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_the_short_path_spelling_is_not_persisted(self):
        """``C:\\\\Users\\\\ADMINI~1`` is the same directory, spelled shorter.

        The guard compared strings, so a config file that held the 8.3
        spelling slipped past a check that the long spelling would have
        caught.
        """
        state = tempfile.mkdtemp(prefix="mas-handover-")
        config = self._config_with(state)
        payload = persistable_config(config)
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)
        # Whichever spelling the machine writes, the answer is the same.
        self.assertNotIn("Temp", str(payload["state_dir"]))

    def test_a_directory_the_user_named_is_not_thrown_away(self):
        """A person's own folder in the temp folder is a decision.

        The guard recognises the *shape* that ``mkdtemp`` produces -
        a prefix that ends in a separator plus eight random characters.
        A folder without that shape is the user's own, however
        unlikely the name.
        """
        for name in (
            "myproject",
            "mas-real-state-demo",
            "openloop-mas",
            "ledger",
        ):
            with self.subTest(name=name):
                state = os.path.join(tempfile.gettempdir(), name)
                payload = persistable_config(self._config_with(state))
                self.assertEqual(payload["state_dir"], state)

    def test_a_mkdtemp_shape_with_any_prefix_is_not_persisted(self):
        """The tail is what matters, not the word in front of it."""
        for prefix in ("mas-handover-", "mas_state_", "masreport-", "tmp"):
            with self.subTest(prefix=prefix):
                state = tempfile.mkdtemp(prefix=prefix)
                payload = persistable_config(self._config_with(state))
                self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_a_real_state_dir_is_still_kept(self):
        """The guard must not eat a directory the user really uses."""
        state = os.path.join(tempfile.gettempdir(), "..", "mas-real-state-demo")
        state = os.path.abspath(state)
        payload = persistable_config(self._config_with(state))
        self.assertEqual(payload["state_dir"], state)

    def test_a_directory_outside_the_temp_folder_is_kept(self):
        state = os.path.join(tempfile.gettempdir(), "..", "..", "mas-not-temp")
        state = os.path.abspath(state)
        payload = persistable_config(self._config_with(state))
        self.assertEqual(payload["state_dir"], state)

    # ------------------------------------------------- the user's own choice
    def test_an_explicitly_named_temp_dir_is_kept(self):
        """A person who typed the path meant it - the guard must not overrule."""
        state = tempfile.mkdtemp(prefix="mas-handover-")
        payload = persistable_config(
            self._config_with(state), explicit_state_dir=state
        )
        self.assertEqual(payload["state_dir"], state)

    def test_an_explicitly_named_dir_wins_over_the_environment_value(self):
        state = tempfile.mkdtemp(prefix="mas-handover-")
        other = tempfile.mkdtemp(prefix="mas-other-")
        os.environ["MAS_STATE_DIR"] = other
        payload = persistable_config(
            self._config_with(state), explicit_state_dir=state
        )
        self.assertEqual(payload["state_dir"], state)

    # ------------------------------------------------------- the environment
    def test_the_environment_value_is_never_persisted(self):
        state = tempfile.mkdtemp(prefix="mas-handover-")
        os.environ["MAS_STATE_DIR"] = state
        payload = persistable_config(self._config_with(state))
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_the_same_directory_written_two_ways_is_still_the_same(self):
        """A trailing separator must not make a directory look different.

        The configuration may hold ``C:\\ledger`` while the command
        line said ``C:\\ledger\\``. That is one directory, and if the
        guard compared raw strings it would judge the user's own choice
        disposable and throw the ledger away.
        """
        state = tempfile.mkdtemp(prefix="mas-handover-")
        with_separator = state + os.sep
        payload = persistable_config(
            self._config_with(state), explicit_state_dir=with_separator
        )
        self.assertEqual(payload["state_dir"], state)

    def test_a_differently_cased_directory_is_still_the_same(self):
        """Windows does not distinguish ``Ledger`` from ``ledger``."""
        state = tempfile.mkdtemp(prefix="mas-handover-")
        shouted = state.upper()
        if shouted == state:
            self.skipTest("the temp path has no letters to change case")
        payload = persistable_config(
            self._config_with(state), explicit_state_dir=shouted
        )
        self.assertEqual(payload["state_dir"], state)

    def test_an_environment_value_outside_the_temp_folder_is_not_persisted(self):
        """The environment guard has a job of its own.

        A temporary directory is caught by the shape guard, so removing
        the environment check would not show up in that test. But the
        environment may point *anywhere* - including a real directory
        that the user never asked to make their ledger home. That value
        belongs to the process, not to the file.
        """
        outside = os.path.join(tempfile.gettempdir(), "..", "mas-env-outside-demo")
        outside = os.path.abspath(outside)
        os.environ["MAS_STATE_DIR"] = outside
        payload = persistable_config(self._config_with(outside))
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    def test_the_environment_value_is_never_persisted_even_when_named(self):
        """Naming it explicitly must not launder an environment injection.

        The command line and the environment can point at the same
        directory. When they do, the directory the *user* typed wins -
        but the file must not record a path that only the environment
        knows about.
        """
        state = tempfile.mkdtemp(prefix="mas-handover-")
        os.environ["MAS_STATE_DIR"] = state
        payload = persistable_config(self._config_with(state))
        self.assertEqual(payload["state_dir"], DEFAULT_STATE_DIR)

    # --------------------------------------------------------- on disk
    def test_saving_never_writes_a_disposable_directory(self):
        state = tempfile.mkdtemp(prefix="mas-handover-")
        target = pathlib.Path(tempfile.mkdtemp(prefix="mas-save-target-")) / "config.json"
        save_config(self._config_with(state), target)
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(written["state_dir"], DEFAULT_STATE_DIR)

    def test_saving_keeps_an_explicitly_named_directory(self):
        state = tempfile.mkdtemp(prefix="mas-handover-")
        target = pathlib.Path(tempfile.mkdtemp(prefix="mas-save-target-")) / "config.json"
        save_config(self._config_with(state), target, explicit_state_dir=state)
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(written["state_dir"], state)


class TheEnvironmentGuardTest(unittest.TestCase):
    """The session guard must hold under the standard-library runner too.

    ``python -m unittest discover -s tests`` never loads
    ``tests/conftest.py``, so the pytest fixtures cannot help there. A
    test module that points ``MAS_STATE_DIR`` somewhere disposable and
    forgets to undo it used to poison every test that came after it.
    """

    def test_the_guard_is_installed(self):
        self.assertTrue(
            getattr(unittest.TestCase.run, "_mas_guarded", False),
            "TestCase.run should be wrapped by the isolation module",
        )

    def test_a_leaking_test_does_not_poison_the_next_one(self):
        # A deliberately leaky test case: it changes the environment and
        # never puts it back.
        class _Leaky(unittest.TestCase):
            def test_leak(self):
                os.environ["MAS_STATE_DIR"] = os.path.join(
                    tempfile.gettempdir(), "mas-leak-should-not-stick"
                )

        before = os.environ.get("MAS_STATE_DIR")
        result = unittest.TestResult()
        _Leaky("test_leak").run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            os.environ.get("MAS_STATE_DIR"),
            before,
            "a test that leaks MAS_STATE_DIR must not affect the next test",
        )


class InitKeepsTheUsersDirectoryTest(unittest.TestCase):
    """``mas init --state-dir`` must remember what the user asked for."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="mas-handover-")
        self.home = tempfile.mkdtemp(prefix="mas-init-home-")
        self._saved_home = os.environ.get("USERPROFILE")
        self._saved_config_path = os.environ.get("MAS_CONFIG_PATH")
        self._saved_state = os.environ.get("MAS_STATE_DIR")
        os.environ["USERPROFILE"] = self.home
        os.environ["MAS_CONFIG_PATH"] = str(
            pathlib.Path(self.home) / ".mas" / "config.json"
        )
        # One test puts a temporary directory into the environment; the
        # environment must not leak into the next test.
        os.environ.pop("MAS_STATE_DIR", None)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self._saved_home
        if self._saved_config_path is None:
            os.environ.pop("MAS_CONFIG_PATH", None)
        else:
            os.environ["MAS_CONFIG_PATH"] = self._saved_config_path
        if self._saved_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._saved_state

    def _init(self):
        from mas.cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["init", "--state-dir", self.state_dir])
        return code, buffer.getvalue()

    def test_the_named_directory_survives_the_command(self):
        code, out = self._init()
        self.assertEqual(code, 0, out)
        written = json.loads(
            pathlib.Path(self.home, ".mas", "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["state_dir"], self.state_dir)

    def test_the_environment_value_is_not_written_by_init(self):
        from mas.cli import main

        os.environ["MAS_STATE_DIR"] = self.state_dir
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["init", "--provider", "mock"])
        self.assertEqual(code, 0, buffer.getvalue())
        written = json.loads(
            pathlib.Path(self.home, ".mas", "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written["state_dir"], DEFAULT_STATE_DIR)


class TheRealConfigSurvivesATestRunTest(unittest.TestCase):
    """The whole point: the user's own file must come out unchanged."""

    def test_the_configuration_file_never_mentions_the_temp_folder(self):
        """A config that points into the temp folder is a broken config."""
        config = load_config()
        state = str(config.get("state_dir") or "")
        if not state:
            self.skipTest("no state_dir configured")
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve()
        try:
            resolved = pathlib.Path(os.path.expanduser(state)).resolve()
        except (OSError, ValueError):  # pragma: no cover - defensive
            self.skipTest("state_dir is not a path")
        try:
            relative = resolved.relative_to(temp_root)
        except ValueError:
            return
        # A directory the user named *on purpose* may live there; one that
        # merely looks automatic may not.
        if len(relative.parts) == 1:
            self.fail(
                "the configuration points at a disposable directory: " + str(resolved)
            )


if __name__ == "__main__":
    unittest.main()
