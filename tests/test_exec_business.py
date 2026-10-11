# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the executive business tools against a synthetic console."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.http import CallLog, ConsoleClient  # noqa: E402
from gateway_insights_core.markdown import NO_ACCESS  # noqa: E402
from gateway_insights_exec import api, business  # noqa: E402
from gateway_insights_exec.raw_tools import ToolContext  # noqa: E402
from gateway_insights_exec.server import build_server  # noqa: E402

TODAY = date(2026, 10, 10)


def top_row(ident, name, now, base, **extra):
    row = {"isGrandTotal": 0, "entityId": ident, "entityName": name, "entityType": "MERCHANT",
           "metricValue": str(now), "metricValuePrev": str(base), "metricDirection": 1,
           "flowApprovedAmount": str(now), "flowApprovedAmountPrev": str(base),
           "flowApprovalRate": "0.9", "flowApprovalRatePrev": "0.92",
           "flowDeclineRate": "0.1", "flowDeclineRatePrev": "0.08", "flowIssuerPct": "0.6", "flowProcessorPct": "0.3",
           "flowManagerRate": "5", "flowBankCost": "2", "flowManagerMargin": "3", "flowManagerMarginPrev": "2",
           "flowResellerMargin": "1", "flowResellerMarginPrev": "1"}
    row.update(extra)
    return row


def grand(rows, **extra):
    total = {"isGrandTotal": 1, "entityId": None, "entityName": "GRAND_TOTAL", "metricValuePrev": None}
    for key in ("flowApprovedAmount", "flowApprovedAmountPrev", "flowManagerMargin", "flowManagerMarginPrev",
                "flowResellerMargin", "flowResellerMarginPrev", "metricValue"):
        values = [Decimal(r[key]) for r in rows if r.get(key) is not None]
        total[key] = str(sum(values)) if len(values) == len(rows) else None
    total.update(extra)
    return total


MERCHANTS = [top_row(1, "Alpha", 500, 400), top_row(2, "Beta", 300, 450), top_row(3, "Gamma", 120, 0),
             top_row(4, "Delta", 0, 80)]
KPI = {"displayCurrency": "EUR", "depositApprovedAmount": "920.00", "depositApprovedAmountPrev": "930.00",
       "depositApprovedAmountDelta": "-10.00", "depositApprovedAmountDeltaPct": "-0.0108",
       "depositApprovedAmountDir": -1, "depositApprovalRate": "0.9", "depositApprovalRatePrev": "0.92",
       "depositApprovalRateDeltaPp": "-0.02", "depositApprovalRateDir": -1, "depositDeclined": 12,
       "depositDeclinedPrev": 9, "depositDeclinedDelta": 3, "depositDeclinedDir": -1}
TILES = [{"type": "SALE", "approvedCountFormatted": "1 000"}, {"type": "CHARGEBACK", "approvedCountFormatted": "12",
          "amountFormatted": "1 200.00", "amountCurrencyCode": "EUR"}, {"type": "FRAUD", "approvedCountFormatted": "3"}]


class FakeConsole:
    """Answers each allowlisted path from the request body; records every call."""

    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def answer(self, path, body):
        if path in self.overrides:
            return self.overrides[path](body)
        if path == api.KPI:
            return KPI
        if path == api.TOP:
            rows = [dict(r, entityType=body["entityType"]) for r in MERCHANTS]
            if body.get("topN"):
                rows = rows[:body["topN"]]
            return {"rows": rows + [grand(rows)]}
        if path == api.TREND:
            ids = body.get("selectedEntityIds") or []
            pts = []
            for day, (a, b) in enumerate([(10, 8), (12, 9), (6, 9)], start=1):
                pts.append({"periodFlag": "current", "bucketKey": 20261000 + day, "seriesId": 0, "seriesKind": "total",
                            "trendNumDeposit": str(a), "trendNumWithdrawal": None})
                pts.append({"periodFlag": "previous", "bucketKey": 20260920 + day, "seriesId": 0, "seriesKind": "total",
                            "trendNumDeposit": str(b), "trendNumWithdrawal": None})
                for i in ids:
                    pts.append({"periodFlag": "current", "bucketKey": 20261000 + day, "seriesId": i,
                                "seriesKind": "entity", "seriesName": f"{i} - E{i}",
                                "trendNumDeposit": str(a * i), "trendNumWithdrawal": None})
            return {"rows": pts}
        if path == api.HOLD_TOP:
            rows = [{"isGrandTotal": 0, "entityId": 1, "entityName": "Alpha", "merchantHold": "700",
                     "bankHold": "100", "displayCurrency": "EUR"},
                    {"isGrandTotal": 1, "entityId": None, "entityName": "GRAND_TOTAL", "merchantHold": "1000",
                     "bankHold": "150", "displayCurrency": "EUR"}]
            return {"rows": rows}
        if path == api.HOLD_SCHEDULE:
            return {"rows": [{"releaseDay": 20261012, "merchantHold": "100", "bankHold": "10", "displayCurrency": "EUR"},
                             {"releaseDay": 20261025, "merchantHold": "300", "bankHold": "20", "displayCurrency": "EUR"},
                             {"releaseDay": None, "merchantHold": "600", "bankHold": "120", "displayCurrency": "EUR"}]}
        if path == api.MONITOR_TOTAL:
            return {"tiles": TILES}
        if path in (api.MONITOR_MERCHANTS, api.MONITOR_PROVIDERS, api.MONITOR_MANAGERS):
            counts = {"SALE": ["600", "400"], "CHARGEBACK": ["9", "2"], "FRAUD": ["1", "0"]}[body["transactionType"]]
            return {"items": [{"merccMercId": 1, "merchantName": "Alpha", "approvedCountFormatted": counts[0]},
                              {"merccMercId": 2, "merchantName": "Beta", "approvedCountFormatted": counts[1]}]}
        raise AssertionError(path)

    def __call__(self, request, timeout, context):
        path = request.full_url.split("/api", 1)[1]
        body = json.loads(request.data)
        self.calls.append((path, body))
        return json.dumps(self.answer(path, body)).encode()


def server_for(console, report_dir=None, theme=None):
    profile = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "z" * 20,
                      report_dir=report_dir or tempfile.gettempdir(), theme=theme)

    def context(name):
        client = ConsoleClient(profile, api.ALLOWED_PATHS, opener=console, log=CallLog())
        return ToolContext(api.ExecApi(client), TODAY, None)
    return build_server(context)


def call(server, name, **arguments):
    server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})["result"]


PERIOD = {"date_from": "2026-10-01", "date_to": "2026-10-07"}


class FormulaTest(unittest.TestCase):
    def test_movers_change_and_contribution(self):
        items = {i["row"]["entityId"]: i for i in business.movers(MERCHANTS + [grand(MERCHANTS)])}
        self.assertEqual(items[1]["change"], Decimal(100))
        # a gain's share of all gains, a fall's share of all falls — never of the net change
        self.assertEqual(items[1]["contribution"], Decimal(100) / Decimal(100 + 120))
        self.assertEqual(items[2]["contribution"], Decimal(-150) / Decimal(-150 - 80))
        self.assertGreater(items[2]["contribution"], 0)

    def test_relative_change_keeps_up_as_up_for_a_negative_base(self):
        from gateway_insights_exec.business_base import rel_change
        self.assertEqual(rel_change(Decimal(-760), Decimal(-800)), Decimal(40) / Decimal(800))
        self.assertIsNone(rel_change(Decimal(1), Decimal(0)))
        self.assertIsNone(business.share(Decimal(5), Decimal(-10)))

    def test_classify_new_left_gained_lost(self):
        groups = business.classify(MERCHANTS, Decimal(0))
        self.assertEqual([i["row"]["entityId"] for i in groups["new"]], [3])
        self.assertEqual([i["row"]["entityId"] for i in groups["left"]], [4])
        self.assertEqual([i["row"]["entityId"] for i in groups["gained"]], [1])
        self.assertEqual([i["row"]["entityId"] for i in groups["lost"]], [2])
        self.assertEqual(business.classify(MERCHANTS, Decimal(200))["gained"], [])

    def test_concentration(self):
        c = business.concentration([Decimal(60), Decimal(30), Decimal(10), Decimal(-5), Decimal(0)])
        self.assertEqual(c["top1"], Decimal("0.6"))
        self.assertEqual(c["top5"], Decimal(1))
        self.assertEqual(c["hhi"], Decimal("0.36") + Decimal("0.09") + Decimal("0.01"))
        self.assertEqual(c["entities"], Decimal(3))
        self.assertIsNone(business.concentration([Decimal(0)])["hhi"])

    def test_released_within_counts_days_before_the_limit(self):
        rows = FakeConsole().answer(api.HOLD_SCHEDULE, {})["rows"]
        self.assertEqual(business.released_within(rows, TODAY, 7, "merchantHold"), Decimal(100))
        self.assertEqual(business.released_within(rows, TODAY, 30, "merchantHold"), Decimal(400))

    def test_total_margin_is_never_filled_in(self):
        row = top_row(1, "A", 1, 1, flowResellerMargin=None)
        self.assertIsNone(business.tier_margin(row, "TOTAL"))
        self.assertEqual(business.tier_margin(row, "MANAGER"), Decimal(3))


class ToolsTest(unittest.TestCase):
    def run_tool(self, name, console=None, **args):
        console = console or FakeConsole()
        res = call(server_for(console), name, **{**PERIOD, **args})
        self.assertFalse(res["isError"], res["content"][0]["text"])
        return res, console

    def assert_provenance(self, res, console):
        spec = res["structuredContent"]
        self.assertEqual(len(spec["provenance"]), len(console.calls))
        self.assertLessEqual(len(console.calls), 8)
        self.assertIn("How obtained", res["content"][0]["text"])

    def test_briefing(self):
        res, console = self.run_tool("ceo_briefing")
        self.assert_provenance(res, console)
        spec = res["structuredContent"]
        self.assertIn("deposits approved amount", spec["summary"][1])
        self.assertEqual({c[0] for c in console.calls}, {api.KPI, api.TOP, api.MONITOR_TOTAL})
        titles = [s["title"] for s in spec["sections"]]
        self.assertIn("Chargebacks, fraud and reversals", titles)

    def test_earnings_effective_rate_and_hidden_reseller(self):
        def top(body):
            rows = [top_row(1, "Alpha", 500, 400, flowResellerMargin=None, flowResellerMarginPrev=None),
                    top_row(2, "Beta", 300, 450, flowManagerMargin="12", flowResellerMargin=None,
                            flowResellerMarginPrev=None)]
            return {"rows": rows + [grand(rows)]}
        res, console = self.run_tool("ceo_earnings", FakeConsole({api.TOP: top}), tier="MANAGER")
        self.assert_provenance(res, console)
        spec = res["structuredContent"]
        margin = Decimal(spec["kpis"][0]["value"])
        amount = Decimal(spec["kpis"][1]["value"])
        self.assertEqual(Decimal(spec["kpis"][2]["value"]), margin / amount)   # effective rate = margin / amount
        # the reads run in parallel: their order in the log is not fixed
        self.assertIn("MANAGER_MARGIN", {b.get("metricMode") for p, b in console.calls if p == api.TOP})
        res2, _ = self.run_tool("ceo_earnings", FakeConsole({api.TOP: top}), tier="TOTAL")
        self.assertIn(NO_ACCESS, res2["content"][0]["text"])

    def test_movers(self):
        res, _ = self.run_tool("ceo_movers")
        self.assertIn("1 grew, 1 fell, 1 new, 1 left", res["structuredContent"]["summary"][0])

    def test_holds(self):
        res, console = self.run_tool("ceo_holds", days=30)
        kpis = {k["label"]: k["value"] for k in res["structuredContent"]["kpis"]}
        self.assertEqual(kpis["Released to merchants in 7 days"], "100")
        self.assertEqual(kpis["Released to merchants in 30 days"], "400")
        self.assertEqual({c[0] for c in console.calls}, {api.HOLD_TOP, api.HOLD_SCHEDULE})

    def test_holds_short_window_still_reads_30_days_for_the_headline(self):
        res, console = self.run_tool("ceo_holds", days=7)
        self.assertEqual([b["days"] for p, b in console.calls if p == api.HOLD_SCHEDULE], [30])
        kpis = {k["label"]: k["value"] for k in res["structuredContent"]["kpis"]}
        self.assertEqual(kpis["Released to merchants in 7 days"], "100")
        self.assertEqual(kpis["Released to merchants in 30 days"], "400")
        self.assertIn("400.00", res["structuredContent"]["summary"][0])
        # The chart keeps to the 7 days asked: one release day, so no chart at all.
        self.assertFalse(any(s.get("chart") for s in res["structuredContent"]["sections"]))

    def test_holds_long_window_reads_the_window(self):
        _, console = self.run_tool("ceo_holds", days=90)
        self.assertEqual([b["days"] for p, b in console.calls if p == api.HOLD_SCHEDULE], [90])

    def test_entity_card_filters_by_the_entity(self):
        res, console = self.run_tool("ceo_entity", entity_type="MERCHANT", entity_id=7)
        for path, body in console.calls:
            self.assertEqual(body["searchFilters"], [{"entityType": "MERCHANT", "items": [{"id": 7, "name": ""}]}])
        self.assertTrue(any(s.get("chart") for s in res["structuredContent"]["sections"]))

    def test_compare(self):
        res, _ = self.run_tool("ceo_compare", entity_type="MERCHANT", entity_ids=[1, 2])
        spec = res["structuredContent"]
        chart = next(s["chart"] for s in spec["sections"] if s.get("chart"))
        self.assertEqual(len(chart["series"]), 2)
        bad = call(server_for(FakeConsole()), "ceo_compare", entity_type="MERCHANT", entity_ids=[1])
        self.assertTrue(bad["isError"])

    def test_split_respects_the_call_budget(self):
        res, console = self.run_tool("ceo_split", entity_type="MERCHANT", entity_ids=[1, 2, 3, 4, 5, 6])
        self.assertEqual(len(console.calls), 8)
        too_many = call(server_for(FakeConsole()), "ceo_split", entity_ids=[1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(too_many["isError"])

    def test_risk_ratio_and_flag(self):
        res, _ = self.run_tool("ceo_risk", threshold=0.01)
        spec = res["structuredContent"]
        ratio_kpi = next(k for k in spec["kpis"] if k["label"].startswith("Chargeback ratio"))
        self.assertEqual(Decimal(ratio_kpi["value"]), Decimal(12) / Decimal(1000))
        rows = next(s["table"]["rows"] for s in spec["sections"])
        alpha = next(r for r in rows if r["entity"].startswith("1 "))
        self.assertEqual(Decimal(alpha["ratio"]), Decimal(9) / Decimal(600))
        self.assertEqual(alpha["flag"], "above threshold")

    def test_report_file_is_written_in_the_profile_theme(self):
        with tempfile.TemporaryDirectory() as folder:
            res = call(server_for(FakeConsole(), report_dir=folder), "ceo_briefing", report=True, **PERIOD)
            self.assertFalse(res["isError"], res)
            pages = list(Path(folder).glob("ceo_briefing-*.html"))
            self.assertEqual(len(pages), 1)
            page = pages[0].read_text()
            self.assertIn("Content-Security-Policy", page)
            self.assertNotIn("<script", page)


class LayeringTest(unittest.TestCase):
    def test_business_code_has_no_http_of_its_own(self):
        root = Path(business.__file__).parent
        for name in ("business.py", "business_base.py"):
            text = (root / name).read_text()
            for forbidden in ("urllib", "http.client", "socket", ".post(", "ConsoleClient("):
                self.assertNotIn(forbidden, text, f"{name} uses {forbidden}")


if __name__ == "__main__":
    unittest.main()
