"""MCP server tests: handshake, tool listing and tool calls."""

import json
import os
import tempfile
import unittest

from mas import mcp_server


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MAS_STATE_DIR"] = self.tmp.name
        os.environ["MAS_PROVIDER"] = "mock"

    def tearDown(self):
        os.environ.pop("MAS_STATE_DIR", None)
        os.environ.pop("MAS_PROVIDER", None)
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
