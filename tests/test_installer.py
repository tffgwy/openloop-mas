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
        self.assertIn('"-m", "mas", "serve"', text)

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
        self.assertEqual(data["mcpServers"]["mas_loop"]["args"], ["-m", "mas", "serve"])

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
