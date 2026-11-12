"""MCP server tests: handshake, tool listing and tool calls."""
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


import json
import os
import tempfile
import unittest

from mas import mcp_server


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Save, do not pop: the session-wide isolation installs
        # ``MAS_PROVIDER=mock`` and a blind ``pop`` in tearDown would
        # remove it for every test that runs afterwards.
        self._old_state = os.environ.get("MAS_STATE_DIR")
        self._old_provider = os.environ.get("MAS_PROVIDER")
        os.environ["MAS_STATE_DIR"] = self.tmp.name
        os.environ["MAS_PROVIDER"] = "mock"

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("MAS_STATE_DIR", None)
        else:
            os.environ["MAS_STATE_DIR"] = self._old_state
        if self._old_provider is None:
            os.environ.pop("MAS_PROVIDER", None)
        else:
            os.environ["MAS_PROVIDER"] = self._old_provider
        self.tmp.cleanup()

    def test_initialize_handshake(self):
        response = mcp_server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "openloop-mas")
        self.assertIn("protocolVersion", result)
        self.assertIn("tools", result["capabilities"])

    def test_tools_list(self):
        response = mcp_server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        for expected in ("mas_run", "mas_status", "mas_ledger", "mas_tickets", "mas_doctor"):
            self.assertIn(expected, names)

    def test_tools_call_run(self):
        response = mcp_server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "mas_run",
                    "arguments": {
                        "goal": "写一段短文",
                        "criteria": [
                            {"id": "c1", "desc": "包含", "check": {"type": "contains", "arg": "关键结论"}},
                            {"id": "c2", "desc": "长度", "check": {"type": "min_chars", "arg": 220}},
                        ],
                    },
                },
            }
        )
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "closed")
        self.assertFalse(response["result"]["isError"])

    def test_unknown_tool_reports_error_without_crashing(self):
        response = mcp_server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}}
        )
        self.assertTrue(response["result"]["isError"])

    def test_notification_returns_no_response(self):
        self.assertIsNone(mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
