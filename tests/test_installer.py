"""Tests for the cross-platform self-installer (``mas install``).

The installer is what makes ``pip install openloop-mas`` a *working*
setup: it copies the agent skills into the hosts' skill directories and
registers the MCP server. These tests pin the rules that matter:

* the skills actually land in the target directories;
* the MCP registration is written correctly;
* running it twice does not duplicate anything;
* an existing configuration is backed up before it is modified;
* a failure to write one host does not take the whole install down.
"""

try:  # pragma: no cover - the two import styles cover two runners
    from ._isolation import ISOLATION_ACTIVE  # noqa: F401
except ImportError:  # loaded as a top-level module (unittest -s tests)
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _isolation import ISOLATION_ACTIVE  # noqa: F401


import json
import pathlib
import sys
import tempfile
import unittest

from mas import installer


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-install-test-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        self._orig_home = installer._home
        installer._home = lambda: self.home
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def test_skills_source_is_found(self):
        source = installer.skills_source()
        self.assertIsNotNone(source)
        self.assertTrue(source.is_dir())

    def test_skills_are_copied_into_the_targets(self):
        report = installer.install_skills()
        self.assertTrue(report["installed"])
        for name in installer.PACKAGED_SKILLS:
            dest = self.home / ".codex" / "skills" / name / "SKILL.md"
            self.assertTrue(dest.exists(), f"{name} SKILL.md missing")
            self.assertIn("---", dest.read_text(encoding="utf-8"))

    def test_claude_skills_are_installed_too(self):
        installer.install_skills()
        dest = self.home / ".claude" / "skills" / "mas-loop" / "SKILL.md"
        self.assertTrue(dest.exists())

    def test_mcp_is_registered_in_the_codex_config(self):
        state, _detail = installer._register_codex(sys.executable)
        self.assertEqual(state, "registered")
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[mcp_servers.mas_loop]", text)
        self.assertIn('"serve"', text)
        # The entry point is named by absolute path, not as ``-m mas``:
        # ``python -m`` puts the host's working directory first on
        # ``sys.path``, and a directory containing a ``mas`` package
        # would shadow the installed runtime.
        self.assertNotIn('"-m", "mas"', text)
        self.assertIn("__main__.py", text)

    def test_mcp_registration_is_idempotent(self):
        installer._register_codex(sys.executable)
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "already")
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(text.count("[mcp_servers.mas_loop]"), 1)

    def test_mcp_is_registered_in_the_claude_config(self):
        state, _ = installer._register_claude(sys.executable)
        self.assertEqual(state, "registered")
        data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertIn("mas_loop", data["mcpServers"])
        args = data["mcpServers"]["mas_loop"]["args"]
        self.assertEqual(len(args), 2)
        self.assertEqual(args[1], "serve")
        self.assertTrue(args[0].endswith("__main__.py"), args)
        self.assertTrue(pathlib.Path(args[0]).exists(), args)

    def test_claude_registration_keeps_the_existing_config(self):
        config = self.home / ".claude.json"
        config.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        installer._register_claude(sys.executable)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("mas_loop", data["mcpServers"])

    def test_existing_config_is_backed_up_before_modification(self):
        config = self.home / ".claude.json"
        config.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        installer._register_claude(sys.executable)
        backups = list(self.home.glob(".claude.json.bak-*"))
        self.assertTrue(backups, "no backup was written")

    def test_a_broken_claude_config_does_not_crash_the_install(self):
        config = self.home / ".claude.json"
        config.write_text("{not json", encoding="utf-8")
        state, _ = installer._register_claude(sys.executable)
        self.assertEqual(state, "registered")
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertIn("mas_loop", data["mcpServers"])

    def test_the_registration_carries_the_configuration_location(self):
        """The MCP server must read *this* installation's configuration.

        Without MAS_CONFIG_PATH the server falls back to the well-known
        ``~/.mas/config.json`` while the launcher reads the installation's
        own file - the CLI and the agent would then use two different
        providers.
        """
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "registered")
        env = installer._codex_block_env(
            (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        )
        self.assertIn("PYTHONPATH", env)
        self.assertIn("MAS_CONFIG_PATH", env)
        self.assertTrue(env["MAS_CONFIG_PATH"].endswith("config.json"))

    def test_the_claude_registration_carries_the_configuration_location(self):
        installer._register_claude(sys.executable)
        data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        env = data["mcpServers"]["mas_loop"]["env"]
        self.assertIn("MAS_CONFIG_PATH", env)
        self.assertIn("MAS_STATE_DIR", env)

    def test_the_env_line_is_parsed_key_by_key(self):
        """A multi-key env table must not swallow its own tail.

        A greedy match produced a garbage PYTHONPATH, which made the
        registration look dead and made every install rewrite it.
        """
        block = installer._codex_block_text(sys.executable)
        env = installer._codex_block_env(block)
        # The TOML table carries forward slashes: a backslash is an escape
        # character inside a TOML basic string.
        self.assertEqual(
            env.get("PYTHONPATH", "").replace("/", "\\"),
            str(installer.package_root().parent),
        )
        self.assertTrue(env.get("MAS_CONFIG_PATH", "").endswith("config.json"))
        self.assertEqual(len(env), 3, env)

    def test_an_old_registration_without_the_config_path_is_refreshed(self):
        """A registration that only sets PYTHONPATH is refreshed.

        It could still start the server, but it would read the wrong
        configuration - "works" is not the same as "right".
        """
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[mcp_servers.mas_loop]\n"
            f'command = "{sys.executable}"\n'
            'args = ["-m", "mas", "serve"]\n'
            f'env = {{ PYTHONPATH = "{installer.package_root().parent}" }}\n',
            encoding="utf-8",
        )
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "updated")
        env = installer._codex_block_env(config.read_text(encoding="utf-8"))
        self.assertIn("MAS_CONFIG_PATH", env)

    def test_a_complete_registration_is_left_alone(self):
        installer._register_codex(sys.executable)
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "already")

    def test_run_install_reports_both_hosts(self):
        report = installer.run_install()
        self.assertEqual(report["mcp"]["codex"]["state"], "registered")
        self.assertEqual(report["mcp"]["claude"]["state"], "registered")
        self.assertTrue(report["skills"]["installed"])
        text = installer.format_report(report)
        self.assertIn("MCP registered (codex)", text)
        self.assertIn("MCP registered (claude)", text)


class UninstallerTest(unittest.TestCase):
    """``mas uninstall`` must remove exactly what ``mas install`` added."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-uninstall-test-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        self._orig_home = installer._home
        installer._home = lambda: self.home
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def test_the_skills_are_removed_again(self):
        installer.install_skills()
        report = installer.run_uninstall()
        self.assertTrue(report["skills"]["removed"])
        for name in installer.PACKAGED_SKILLS:
            self.assertFalse(
                (self.home / ".codex" / "skills" / name).exists(),
                f"{name} was not removed",
            )

    def test_the_codex_registration_is_removed(self):
        installer._register_codex(sys.executable)
        report = installer.run_uninstall()
        self.assertEqual(report["mcp"]["codex"]["state"], "removed")
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("[mcp_servers.mas_loop]", text)

    def test_the_claude_registration_is_removed(self):
        installer._register_claude(sys.executable)
        report = installer.run_uninstall()
        self.assertEqual(report["mcp"]["claude"]["state"], "removed")
        data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertNotIn("mas_loop", data.get("mcpServers") or {})

    def test_the_other_codex_settings_are_kept(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n',
            encoding="utf-8",
        )
        installer._register_codex(sys.executable)
        installer.run_uninstall()
        text = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', text)
        self.assertIn("[mcp_servers.other]", text)
        self.assertNotIn("[mcp_servers.mas_loop]", text)

    def test_uninstalling_twice_is_harmless(self):
        installer.run_install()
        installer.run_uninstall()
        report = installer.run_uninstall()
        self.assertEqual(report["mcp"]["codex"]["state"], "absent")
        self.assertEqual(report["mcp"]["claude"]["state"], "absent")

    def test_the_ledger_is_never_touched(self):
        # The ledger is the history of every closed loop; it is the one
        # thing that cannot be regenerated, so uninstall keeps it.
        state = self.home / ".mas"
        ledger = state / "ledgers" / "runs.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"run_id": "R-1"}\n', encoding="utf-8")
        installer.run_uninstall()
        self.assertTrue(ledger.exists())
        self.assertIn("R-1", ledger.read_text(encoding="utf-8"))

    def test_the_report_mentions_that_the_state_is_kept(self):
        report = installer.run_uninstall()
        text = installer.format_uninstall_report(report)
        self.assertIn("kept", text)


class SkillSyncTest(unittest.TestCase):
    """Drift between the packaged skills and the installed copies.

    The packaged copy is the truth. When they disagree, the agent is
    following a skill that the runtime does not know about - and every
    other check still says "installed". These tests pin the detection
    and the repair.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-sync-test-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        self._orig_home = installer._home
        installer._home = lambda: self.home
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def test_a_fresh_install_has_no_drift(self):
        installer.install_skills()
        report = installer.skill_sync_report()
        rows = report["rows"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["status"], "match", row)

    def test_an_edited_skill_is_reported_as_drift(self):
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        report = installer.skill_sync_report()
        drifted = [r for r in report["rows"] if r["status"] == "drift"]
        self.assertTrue(drifted)
        self.assertEqual(drifted[0]["skill"], "mas-loop")

    def test_a_missing_skill_is_not_drift_but_is_missing(self):
        installer.install_skills()
        import shutil as _shutil

        _shutil.rmtree(self.home / ".codex" / "skills" / "mas-review")
        report = installer.skill_sync_report()
        rows = {r["path"]: r for r in report["rows"]}
        missing = [r for r in rows.values() if r["actual"] == "missing"]
        self.assertTrue(missing)

    def test_the_fingerprint_ignores_the_timestamp(self):
        import os
        import time

        source = installer.skills_source() / "mas-loop"
        before = installer.skill_fingerprint(source)
        os.utime(source, (time.time() - 10000, time.time() - 10000))
        self.assertEqual(installer.skill_fingerprint(source), before)

    def test_dry_run_reports_without_writing(self):
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        report = installer.run_sync(dry_run=True)
        self.assertTrue(report["drift"])
        self.assertEqual(report["fixed"], [])
        self.assertEqual(target.read_text(encoding="utf-8"), "edited by hand\n")

    def test_sync_repairs_the_drift(self):
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        report = installer.run_sync()
        self.assertTrue(report["fixed"])
        self.assertEqual(report["remaining"], [])
        source = installer.skills_source() / "mas-loop" / "SKILL.md"
        self.assertEqual(target.read_text(encoding="utf-8"),
                         source.read_text(encoding="utf-8"))

    def test_sync_backs_up_the_replaced_copy(self):
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        report = installer.run_sync()
        self.assertTrue(report.get("backups"))

    def test_a_crlf_copy_is_not_drift(self):
        """Windows line endings must not read as a content change.

        A checkout with ``core.autocrlf=true`` turns the packaged LF
        files into CRLF. The text is identical, so the copy is *not*
        drift - and a sync must not keep replacing it.
        """
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        raw = target.read_bytes().replace(b"\r\n", b"\n")
        target.write_bytes(raw.replace(b"\n", b"\r\n"))
        report = installer.skill_sync_report()
        drifted = [r for r in report["rows"] if r["status"] == "drift"]
        self.assertEqual(drifted, [], drifted)

    def test_a_skill_backup_never_lands_in_the_skills_root(self):
        """A backup inside a skills root looks like an installed skill.

        Every host that scans its skills directory would offer the
        backup as a skill, and the drift detector would see an unknown
        copy. The backup therefore goes to ``~/.mas/backup/skills``.
        """
        installer.install_skills()
        target = self.home / ".codex" / "skills" / "mas-loop" / "SKILL.md"
        target.write_text("edited by hand\n", encoding="utf-8")
        report = installer.run_sync()
        self.assertTrue(report.get("backups"))
        leftovers = list((self.home / ".codex" / "skills").glob("mas-loop.bak-*"))
        self.assertEqual(leftovers, [], leftovers)
        stored = list((self.home / ".mas" / "backup" / "skills").rglob("mas-loop.bak-*"))
        self.assertTrue(stored, "the backup was not kept outside the skills root")

    def test_sync_on_a_fresh_install_is_a_no_op(self):
        installer.install_skills()
        report = installer.run_sync()
        self.assertEqual(report["drift"], [])
        self.assertIn("无需同步", installer.format_sync_report(report))


class StaleMcpRegistrationTest(unittest.TestCase):
    """A registration that cannot start must not be reported as fine.

    ``mas install`` used to treat "the key exists" as "the registration
    is good". An entry pointing at an interpreter that was deleted - or
    at a package path from an uninstalled copy - makes the MCP server
    fail to start, while every self-check still said "registered".
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-stale-mcp-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        self._orig_home = installer._home
        installer._home = lambda: self.home
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def test_a_dead_interpreter_is_refreshed(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '[mcp_servers.mas_loop]\n'
            'command = "C:/definitely/not/here/python.exe"\n'
            'args = ["-m", "mas", "serve"]\n',
            encoding="utf-8",
        )
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "updated")
        text = config.read_text(encoding="utf-8")
        self.assertIn(sys.executable.replace(chr(92), "/"), text)

    def test_a_live_registration_is_left_alone(self):
        installer._register_codex(sys.executable)
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "already")

    def test_refreshing_keeps_the_other_settings(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'model = "gpt-5"\n\n'
            '[mcp_servers.mas_loop]\n'
            'command = "C:/definitely/not/here/python.exe"\n'
            'args = ["-m", "mas", "serve"]\n\n'
            '[mcp_servers.other]\ncommand = "x"\n',
            encoding="utf-8",
        )
        installer._register_codex(sys.executable)
        text = config.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', text)
        self.assertIn("[mcp_servers.other]", text)
        self.assertEqual(text.count("[mcp_servers.mas_loop]"), 1)

    def test_a_dead_claude_entry_is_refreshed(self):
        config = self.home / ".claude.json"
        config.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "mas_loop": {
                            "command": "C:/definitely/not/here/python.exe",
                            "args": ["-m", "mas", "serve"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        state, _ = installer._register_claude(sys.executable)
        self.assertEqual(state, "updated")
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(
            data["mcpServers"]["mas_loop"]["command"].replace(chr(92), "/"),
            sys.executable.replace(chr(92), "/"),
        )

    def test_a_live_claude_entry_is_left_alone(self):
        installer._register_claude(sys.executable)
        state, _ = installer._register_claude(sys.executable)
        self.assertEqual(state, "already")

    def test_the_report_mentions_a_refresh(self):
        report = {
            "mcp": {
                "codex": {"state": "updated", "detail": "x"},
                "claude": {"state": "already", "detail": "y"},
            }
        }
        text = installer.format_report(report)
        self.assertIn("refreshed", text)


class UsableRegistrationTest(unittest.TestCase):
    """A registration is judged by whether it works, not by equality.

    ``mas install`` runs from wherever the package happens to live. If a
    registration were compared against the *currently imported* module,
    a perfectly good installation in ``~/.mas/app`` would be declared
    broken every time the tool ran from a source checkout.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mas-usable-")
        self.addCleanup(self._tmp.cleanup)
        self.home = pathlib.Path(self._tmp.name)
        self._orig_home = installer._home
        installer._home = lambda: self.home
        self.addCleanup(lambda: setattr(installer, "_home", self._orig_home))

    def test_a_registration_pointing_at_a_real_package_is_usable(self):
        other = self.home / "other-app"
        (other / "mas").mkdir(parents=True)
        (other / "mas" / "__init__.py").write_text("", encoding="utf-8")
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '[mcp_servers.mas_loop]\n'
            f'command = "{sys.executable.replace(chr(92), "/")}"\n'
            'args = ["-m", "mas", "serve"]\n'
            f'env = {{ PYTHONPATH = "{str(other).replace(chr(92), "/")}" }}\n',
            encoding="utf-8",
        )
        state, _ = installer._register_codex(sys.executable)
        # The registration can start the server, but it does not say which
        # configuration to read. It is therefore refreshed - "works" is not
        # the same as "works against the right configuration".
        self.assertEqual(state, "updated")
        env = installer._codex_block_env(config.read_text(encoding="utf-8"))
        self.assertIn("MAS_CONFIG_PATH", env)

    def test_a_registration_without_a_package_path_falls_back_to_the_interpreter(self):
        # A pip-installed package needs no PYTHONPATH; the interpreter
        # itself can import mas.
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '[mcp_servers.mas_loop]\n'
            f'command = "{sys.executable.replace(chr(92), "/")}"\n'
            'args = ["-m", "mas", "serve"]\n',
            encoding="utf-8",
        )
        state, _ = installer._register_codex(sys.executable)
        # A pip-installed package needs no PYTHONPATH - but the
        # registration still has to name the configuration to read.
        self.assertEqual(state, "updated")
        env = installer._codex_block_env(config.read_text(encoding="utf-8"))
        self.assertIn("MAS_CONFIG_PATH", env)

    def test_a_registration_pointing_at_a_missing_package_is_refreshed(self):
        config = self.home / ".codex" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            '[mcp_servers.mas_loop]\n'
            f'command = "{sys.executable.replace(chr(92), "/")}"\n'
            'args = ["-m", "mas", "serve"]\n'
            'env = { PYTHONPATH = "C:/definitely/not/here" }\n',
            encoding="utf-8",
        )
        state, _ = installer._register_codex(sys.executable)
        self.assertEqual(state, "updated")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
