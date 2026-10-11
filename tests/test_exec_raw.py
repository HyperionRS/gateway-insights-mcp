# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the executive server's raw layer and tools. Fixtures are synthetic."""

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
from gateway_insights_core.http import ConsoleClient  # noqa: E402
from gateway_insights_core.markdown import NO_ACCESS  # noqa: E402
from gateway_insights_exec import api, raw_tools  # noqa: E402
from gateway_insights_exec.raw_tools import ToolContext  # noqa: E402
from gateway_insights_exec.server import build_server  # noqa: E402

TODAY = date(2026, 10, 10)


class Recorder:
    """Answers by path from a dict of canned JSON; records every request body."""

    def __init__(self, answers):
        self.answers = answers
        self.bodies = []

    def __call__(self, request, timeout, context):
        path = request.full_url.split("/api", 1)[1]
        self.bodies.append((path, json.loads(request.data)))
        return json.dumps(self.answers[path]).encode()


def context_for(recorder, currency_id=None):
    profile = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "x" * 20,
                      currency_id=currency_id)
    client = ConsoleClient(profile, api.ALLOWED_PATHS, opener=recorder)
    return lambda name: ToolContext(api.ExecApi(client), TODAY, currency_id)


def call(server, name, **arguments):
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})["result"]


TOP_ROWS = [
    {"isGrandTotal": 0, "entityId": 7, "entityName": "Shop | *one*", "entityType": "MERCHANT",
     "metricValue": "120.00", "metricValuePrev": "100.00", "metricDirection": 1, "flowApproved": 10,
     "flowDeclined": 2, "flowTotal": 12, "flowApprovedAmount": "1000.00", "flowApprovedAmountPrev": "900.00",
     "flowApprovalRate": "0.833333", "flowApprovalRatePrev": "0.8", "flowAvgTicket": "83.33",
     "flowManagerRate": None, "flowManagerRatePrev": None, "flowBankCost": None, "flowBankCostPrev": None,
     "flowManagerMargin": None, "flowManagerMarginPrev": None,
     "flowResellerMargin": "5.00", "flowResellerMarginPrev": "4.00"},
    {"isGrandTotal": 1, "entityId": None, "entityName": "GRAND_TOTAL", "entityType": "MERCHANT",
     "metricValue": "120.00", "metricValuePrev": None, "metricDirection": None, "flowApproved": 10,
     "flowApprovedAmount": "1000.00", "flowApprovalRate": "0.833333",
     "flowManagerRate": None, "flowBankCost": None, "flowManagerMargin": None, "flowResellerMargin": "5.00"},
]


class ScopeTest(unittest.TestCase):
    def test_base_is_sent_only_when_given(self):
        s = api.scope({"date_from": "2026-10-01", "date_to": "2026-10-07"}, TODAY, None)
        self.assertNotIn("baseBankDate", s.body())
        self.assertEqual(str(s.effective_base), "2026-09-24 .. 2026-09-30")
        s = api.scope({"date_from": "2026-10-01", "date_to": "2026-10-07",
                       "base_from": "2025-10-01", "base_to": "2025-10-07", "merchant_ids": [3, 3, 4]}, TODAY, 978)
        body = s.body()
        self.assertEqual(body["baseBankDate"], {"startDate": "2025-10-01", "endDate": "2025-10-07"})
        self.assertEqual(body["toCurrencyId"], 978)
        self.assertEqual(body["searchFilters"], [{"entityType": "MERCHANT",
                                                  "items": [{"id": 3, "name": ""}, {"id": 4, "name": ""}]}])

    def test_hour_grain_with_a_base_is_refused(self):
        with self.assertRaises(InputError):
            api.scope({"grain": "HOUR", "base_from": "2026-09-01", "base_to": "2026-09-02"}, TODAY, None)

    def test_unknown_metric_is_refused_before_the_network(self):
        rec = Recorder({})
        with self.assertRaises(InputError):
            api.ExecApi(ConsoleClient(Profile(name="t", base_url="https://c.example.com", token="pat_" + "y" * 20),
                                      api.ALLOWED_PATHS, opener=rec)).top(
                api.scope({}, TODAY, None), "PROFIT", "BOTH", "MERCHANT")
        self.assertEqual(rec.bodies, [])


class TopTest(unittest.TestCase):
    def test_hidden_tiers_are_no_access_and_never_derived(self):
        rec = Recorder({api.TOP: {"rows": TOP_ROWS}})
        res = call(build_server(context_for(rec)), "exec_top", metric="AMOUNT", entity_type="MERCHANT")
        self.assertFalse(res["isError"], res)
        text = res["content"][0]["text"]
        self.assertIn(NO_ACCESS, text)
        self.assertIn("Shop \\| \\*one\\*", text)
        row = res["structuredContent"]["rows"][0]
        for key in ("flowManagerRate", "flowBankCost", "flowManagerMargin"):
            self.assertIsNone(row[key])
        self.assertEqual(row["flowResellerMargin"], "5.00")
        self.assertEqual(rec.bodies[0][1]["metricMode"], "AMOUNT")
        self.assertEqual(rec.bodies[0][1]["topN"], 10)

    def test_all_entities_sends_no_limit(self):
        rec = Recorder({api.TOP: {"rows": []}})
        call(build_server(context_for(rec)), "exec_top", metric="TOTAL_MARGIN", all=True)
        self.assertNotIn("topN", rec.bodies[0][1])


class TrendTest(unittest.TestCase):
    def test_rates_are_numerator_over_denominator(self):
        self.assertEqual(raw_tools.trend_value("3", "4"), Decimal("0.75"))
        self.assertIsNone(raw_tools.trend_value("3", "0"))
        self.assertEqual(raw_tools.trend_value("12", None), Decimal("12"))

    def test_separate_split_and_selection_are_sent(self):
        rec = Recorder({api.TREND: {"rows": [
            {"periodFlag": "current", "bucketKey": 20261001, "seriesId": 0, "seriesKind": "total",
             "seriesName": "Total", "trendNumDeposit": "9", "trendDenDeposit": "10",
             "trendNumWithdrawal": None, "trendDenWithdrawal": None}]}})
        res = call(build_server(context_for(rec)), "exec_trend", metric="CONVERSION", entity_ids=[1, 2],
                   split="SEPARATE")
        body = rec.bodies[0][1]
        self.assertEqual((body["selectedEntityIds"], body["selectedSplit"]), ([1, 2], "SEPARATE"))
        self.assertIn("90.00%", res["content"][0]["text"])


class KpiAndMonitorTest(unittest.TestCase):
    def test_kpi_table(self):
        data = {"displayCurrency": "EUR", "totalCount": 120, "totalCountPrev": 100, "totalCountDelta": 20,
                "depositTotal": 100, "depositTotalPrev": 90, "depositApproved": 80, "depositApprovedPrev": 70,
                "depositApprovedDelta": 10, "depositApprovedDir": 1, "depositApprovalRate": "0.8",
                "depositApprovalRatePrev": "0.777778", "depositApprovalRateDeltaPp": "0.022222",
                "depositApprovalRateDir": 1, "depositMixIssuer": "0.6"}
        rec = Recorder({api.KPI: data})
        res = call(build_server(context_for(rec)), "exec_kpi", date_from="2026-10-01", date_to="2026-10-07")
        text = res["content"][0]["text"]
        self.assertIn("80.00%", text)
        self.assertIn("+2.22 pp", text)
        self.assertIn("▲ good", text)
        self.assertEqual(rec.bodies[0][1]["bankDate"], {"startDate": "2026-10-01", "endDate": "2026-10-07"})

    def test_monitor_display_values_parse(self):
        tiles = [{"type": "CHARGEBACK", "title": "Chargeback", "approvedCountFormatted": "1 204",
                  "declinedCountFormatted": None, "filteredCountFormatted": None, "amountFormatted": "12 345.60",
                  "amountCurrencyCode": "EUR", "approvalRatioFormatted": None, "countsOnly": False}]
        st = raw_tools.monitor_total_structured(tiles)[0]
        self.assertEqual((st["approved"], st["amount"]), (Decimal("1204"), Decimal("12345.60")))


class ServerShapeTest(unittest.TestCase):
    def test_no_generic_call_tool_and_a_fixed_allowlist(self):
        server = build_server(context_for(Recorder({})))
        names = set(server.tools)
        self.assertFalse(any("call" in n or "request" in n for n in names))
        self.assertEqual(api.ALLOWED_PATHS, frozenset({
            "/exec-dashboard/kpi", "/exec-dashboard/trend", "/exec-dashboard/top", "/exec-dashboard/hold-top",
            "/exec-dashboard/hold-schedule", "/charts/transactionMonitorTotal",
            "/charts/transactionMonitorMerchants", "/charts/transactionMonitorManagers",
            "/charts/transactionMonitorProviders",
            "/merchant/list", "/provider/list", "/manager/list", "/bank-terminal/list", "/requestors/list",
            "/blueprint/list", "/channel/list", "/reseller/list"}))
        for tool in server.tools.values():
            self.assertFalse(tool.input_schema.get("additionalProperties", True))
            for prop in tool.input_schema["properties"]:
                self.assertNotIn(prop, ("path", "url", "host", "base_url"))


if __name__ == "__main__":
    unittest.main()
