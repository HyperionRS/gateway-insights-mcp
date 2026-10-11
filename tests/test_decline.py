# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the decline server against a synthetic console."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.errors import InputError  # noqa: E402
from gateway_insights_core.http import CallLog, ConsoleClient  # noqa: E402
from gateway_insights_decline import api, tools  # noqa: E402
from gateway_insights_decline.server import build_server  # noqa: E402

TODAY = date(2026, 10, 10)
PERIOD = {"date_from": "2026-10-01", "date_to": "2026-10-07"}


def heat_row(ident, name, total, declined, others=False):
    return {"dimensionId": ident, "dimensionName": name, "totalTx": total, "declinedTx": declined,
            "declineRate": str(Decimal(declined) / Decimal(total)), "others": others}


class FakeConsole:
    def __init__(self, verdict="BROAD"):
        self.calls = []
        self.verdict = verdict

    def answer(self, path, body):
        if path == api.KPI:
            base = body["statusChanged"]["startDate"] < "2026-10-01"
            return {"criteriaTotalTx": 1000, "criteriaDeclinedTx": 100 if base else 150,
                    "criteriaDeclineRate": "0.10" if base else "0.15",
                    "referenceTotalTx": 5000, "referenceDeclinedTx": 600, "referenceDeclineRate": "0.12"}
        if path == api.HEATMAP:
            return {"rows": [heat_row(1, "Route A", 400, 20), heat_row(2, "Route B", 300, 60),
                             heat_row(3, "Route C", 10, 9), heat_row(-1, "Others (4)", 290, 61, others=True)]}
        if path == api.COMPARE:
            return {"summary": {"verdict": self.verdict, "declineRate": "0.15", "baseDeclineRate": "0.10",
                                "excessDeclined": "50", "mixEffect": "0.01", "rateEffect": "0.04",
                                "topDimensionName": "Route B", "topExcessShare": "0.4"},
                    "rows": [{"dimensionId": 2, "dimensionName": "Route B", "totalTx": 300, "baseDeclineRate": "0.1",
                              "declineRate": "0.2", "excessDeclined": "30", "zScore": "3.1", "others": False},
                             {"dimensionId": 1, "dimensionName": "Route A", "totalTx": 400, "baseDeclineRate": "0.05",
                              "declineRate": "0.05", "excessDeclined": "0", "zScore": "0", "others": False}]}
        raise AssertionError(path)

    def __call__(self, request, timeout, context):
        path = request.full_url.split("/api", 1)[1]
        body = json.loads(request.data)
        self.calls.append((path, body))
        return json.dumps(self.answer(path, body)).encode()


def server_for(console):
    profile = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "q" * 20)

    def context(name):
        return tools.Context(api.DeclineApi(ConsoleClient(profile, api.ALLOWED_PATHS, opener=console, log=CallLog())),
                             TODAY)
    return build_server(context)


def call(server, name, **arguments):
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})["result"]


class ScopeTest(unittest.TestCase):
    def test_body_carries_criteria_filters_and_direction(self):
        s = api.scope({**PERIOD, "bank_ids": [5, 0], "merchant_ids": [7], "direction": "dst"}, TODAY)
        body = s.common()
        self.assertEqual(body["direction"], "DST")
        self.assertEqual(body["criteria"], {"banks": [{"id": 5, "name": ""}, {"id": 0, "name": ""}]})
        self.assertEqual(body["searchFilters"], [{"entityType": "MERCHANT", "items": [{"id": 7, "name": ""}]}])
        self.assertEqual(str(s.base), "2026-09-24 .. 2026-09-30")

    def test_window_limit(self):
        with self.assertRaises(InputError):
            api.scope({"date_from": "2025-01-01", "date_to": "2026-10-01"}, TODAY)


class ToolsTest(unittest.TestCase):
    def run_tool(self, name, console=None, **args):
        console = console or FakeConsole()
        res = call(server_for(console), name, **{**PERIOD, **args})
        self.assertFalse(res["isError"], res["content"][0]["text"])
        self.assertLessEqual(len(console.calls), 8)
        self.assertEqual(len(res["structuredContent"].get("provenance", console.calls)), len(console.calls))
        return res, console

    def test_overview_ranks_segments_above_reference(self):
        res, console = self.run_tool("decline_overview")
        self.assertEqual(len(console.calls), 7)   # KPI now and base + five card dimensions
        rows = res["structuredContent"]["sections"][0]["table"]["rows"]
        self.assertFalse(any("Route C" in r["segment"] for r in rows))   # 10 transactions: under min_tx
        self.assertFalse(any("Others" in r["segment"] for r in rows))
        b = next(r for r in rows if "Route B" in r["segment"])
        self.assertEqual(Decimal(b["vs"]), Decimal("0.2") - Decimal("0.12"))   # rate − reference
        kpi = res["structuredContent"]["kpis"][0]
        self.assertEqual(kpi["direction"], -1)   # the decline rate rose: bad

    def test_change_reads_broad_as_not_the_cards(self):
        res, _ = self.run_tool("decline_change")
        self.assertIn("unlikely to be the cards", " ".join(res["structuredContent"]["summary"]))
        res2, _ = self.run_tool("decline_change", FakeConsole("CONCENTRATED"))
        summary = " ".join(res2["structuredContent"]["summary"])
        self.assertIn("Concentrated: card country → Route B (40.00% of the excess declines)", summary)

    def test_change_dimension_limit(self):
        res = call(server_for(FakeConsole()), "decline_change", **PERIOD, dimensions=list(api.DIMENSIONS[:9]))
        self.assertTrue(res["isError"])

    def test_route_needs_a_segment_and_estimates_avoidable(self):
        res = call(server_for(FakeConsole()), "decline_route", **PERIOD)
        self.assertTrue(res["isError"])
        res, console = self.run_tool("decline_route", card_country_ids=[276])
        rows = res["structuredContent"]["sections"][0]["table"]["rows"]
        self.assertEqual(rows[0]["segment"], "1 · Route A")
        b = next(r for r in rows if r["segment"] == "2 · Route B")
        self.assertEqual(Decimal(b["avoidable"]), (Decimal("0.2") - Decimal("0.05")) * 300)
        heat = next(b for p, b in console.calls if p == api.HEATMAP)
        self.assertEqual(heat["criteria"], {"cardCountries": [{"id": 276, "name": ""}]})

    def test_payouts_use_the_recipient_card(self):
        _, console = self.run_tool("decline_payouts")
        self.assertTrue(all(b["direction"] == "DST" for _, b in console.calls))

    def test_opportunity_sums_positive_excess(self):
        res, _ = self.run_tool("decline_opportunity", dimension="PROVIDER")
        kpis = {k["label"]: k["value"] for k in res["structuredContent"]["kpis"]}
        self.assertEqual(kpis["In the top 2 segments"], "30")

    def test_raw_tools_and_no_generic_call(self):
        server = server_for(FakeConsole())
        self.assertEqual(api.ALLOWED_PATHS, frozenset({
            "/decline-dashboard/kpi", "/decline-dashboard/heatmap", "/decline-dashboard/compare",
            "/merchant/list", "/provider/list", "/manager/list", "/bank-terminal/list", "/requestors/list",
            "/blueprint/list", "/channel/list", "/reseller/list",
            "/routing-cascading/top-route", "/routing-cascading-route/get", "/routing-cascading/mermaid",
            "/blueprint/restrictions", "/blueprint/restriction-view", "/requestors/restrictions",
            "/requestors/restriction-view", "/bank-terminal/restrictions", "/bank-terminal/restriction-view",
            "/provider/restrictions", "/provider/restriction-view"}))
        for tool in server.tools.values():
            for prop in tool.input_schema["properties"]:
                self.assertNotIn(prop, ("path", "url", "host", "base_url"))
        res = call(server, "decline_heatmap", **PERIOD, dimension="BANK")
        self.assertIn("Route A", res["content"][0]["text"])


class LayeringTest(unittest.TestCase):
    def test_tools_reach_the_console_only_through_the_raw_layer(self):
        text = Path(tools.__file__).read_text()
        for forbidden in ("urllib", "http.client", "socket", ".post(", "ConsoleClient("):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
