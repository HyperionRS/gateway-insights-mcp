# SPDX-License-Identifier: Apache-2.0
"""entity_search: the console's entity lists, in both servers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_core import entities  # noqa: E402
from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.http import CallLog, ConsoleClient  # noqa: E402
from gateway_insights_decline import api as decline_api  # noqa: E402
from gateway_insights_decline import tools as decline_tools  # noqa: E402
from gateway_insights_decline.server import build_server as decline_server  # noqa: E402
from gateway_insights_exec import api as exec_api  # noqa: E402
from gateway_insights_exec.raw_tools import ToolContext  # noqa: E402
from gateway_insights_exec.server import build_server as exec_server  # noqa: E402

PROFILE = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "z" * 20,
                  report_dir=tempfile.gettempdir())
MERCHANTS = [{"merchantId": 7, "name": "Alpha | *Shop*", "status": "ENABLED", "login": "alpha-login",
              "email": "owner@example.com"},
             {"merchantId": 8, "name": "Alphabet", "status": "DISABLED", "login": "b", "email": "b@example.com"},
             {"merchantId": 9, "name": "Alpine", "status": "ENABLED", "login": "c", "email": "c@example.com"}]


class Console:
    def __init__(self):
        self.calls = []

    def __call__(self, request, timeout, context):
        path = request.full_url[len(PROFILE.base_url):]
        body = json.loads(request.data)
        self.calls.append((path, body))
        if path == "/merchant/list":
            rows = [r for r in MERCHANTS if body.get("searchText", "").lower() in r["name"].lower()
                    or body.get("searchText") == str(r["merchantId"])]
            return json.dumps({"sortableFields": ["merchantId"], "merchants": rows[:body["size"]]}).encode()
        if path == "/blueprint/list":
            return json.dumps({"blueprints": [{"blueprintId": 3, "name": "EUR plan", "currencyCode": "EUR",
                                               "status": "ENABLED", "description": "internal"}]}).encode()
        if path == "/provider/list":
            return json.dumps({"providers": [{"providerId": 1, "name": "Acquirer", "status": "ENABLED",
                                              "description": "https://docs.example.com"}]}).encode()
        raise AssertionError(path)


def exec_for(console):
    def context(name):
        return ToolContext(exec_api.ExecApi(ConsoleClient(PROFILE, exec_api.ALLOWED_PATHS, opener=console,
                                                          log=CallLog())), date(2026, 10, 10), None)
    return exec_server(context)


def decline_for(console):
    def context(name):
        return decline_tools.Context(decline_api.DeclineApi(ConsoleClient(PROFILE, decline_api.ALLOWED_PATHS,
                                                                          opener=console, log=CallLog())),
                                     date(2026, 10, 10))
    return decline_server(context)


def call(server, **arguments):
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "entity_search", "arguments": arguments}})["result"]


class EntitySearchTest(unittest.TestCase):
    def test_both_servers_have_it_and_may_call_every_list(self):
        for build, allowed in ((exec_for, exec_api.ALLOWED_PATHS), (decline_for, decline_api.ALLOWED_PATHS)):
            self.assertTrue(entities.PATHS <= allowed)
            res = call(build(Console()), entity_type="MERCHANT", query="alp")
            self.assertFalse(res["isError"], res["content"][0]["text"])

    def test_only_id_name_status_currency_leave_the_tool(self):
        console = Console()
        res = call(exec_for(console), entity_type="MERCHANT", query="alp")
        text = res["content"][0]["text"]
        for secret in ("alpha-login", "owner@example.com"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, json.dumps(res["structuredContent"]))
        self.assertEqual(set(res["structuredContent"]["rows"][0]), {"id", "name", "status", "currency"})
        self.assertIn("Alpha \\| \\*Shop\\*", text)     # a name is data: escaped

    def test_more_is_reported_from_the_extra_row(self):
        console = Console()
        res = call(exec_for(console), entity_type="MERCHANT", query="alp", limit=2)
        self.assertEqual(console.calls[0][1]["size"], 3)
        self.assertTrue(res["structuredContent"]["more"])
        self.assertEqual(len(res["structuredContent"]["rows"]), 2)
        self.assertIn("More than 2 match", res["content"][0]["text"])

    def test_id_query_and_linked_filters_go_to_the_console(self):
        console = Console()
        call(decline_for(console), entity_type="PROVIDER", query="7", merchant_ids=[7])
        path, body = console.calls[0]
        self.assertEqual(path, "/provider/list")
        self.assertEqual(body["searchText"], "7")
        self.assertEqual(body["searchFilters"], [{"entityType": "MERCHANT", "items": [{"id": 7, "name": ""}]}])

    def test_currency_is_kept_and_description_dropped(self):
        res = call(exec_for(Console()), entity_type="BLUEPRINT")
        self.assertEqual(res["structuredContent"]["rows"], [{"id": 3, "name": "EUR plan", "status": "ENABLED",
                                                             "currency": "EUR"}])
        self.assertNotIn("internal", res["content"][0]["text"])

    def test_status_only_where_the_list_has_it(self):
        console = Console()
        self.assertFalse(call(exec_for(console), entity_type="MERCHANT", status="enabled")["isError"])
        self.assertEqual(console.calls[0][1]["status"], "ENABLED")
        res = call(exec_for(console), entity_type="PROVIDER", status="ENABLED")
        self.assertTrue(res["isError"])
        self.assertEqual(len(console.calls), 1)

    def test_bad_input_never_reaches_the_console(self):
        console = Console()
        self.assertTrue(call(exec_for(console), entity_type="PERSON")["isError"])
        self.assertTrue(call(exec_for(console), entity_type="MERCHANT", query="x" * 101)["isError"])
        self.assertEqual(console.calls, [])

    def test_nothing_found(self):
        res = call(exec_for(Console()), entity_type="MERCHANT", query="zzz")
        self.assertIn("Nothing found", res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
