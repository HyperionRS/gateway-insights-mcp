# SPDX-License-Identifier: Apache-2.0
"""Shared reads: a blueprint's routing and an entity's restrictions."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_insights_core import restrictions, routing  # noqa: E402
from gateway_insights_core.config import Profile  # noqa: E402
from gateway_insights_core.errors import InputError  # noqa: E402
from gateway_insights_core.http import CallLog, ConsoleClient  # noqa: E402

PROFILE = Profile(name="t", base_url="https://console.example.com/api", token="pat_" + "r" * 20)
TOP = {"route": {"routeId": 10, "enabled": "ENABLED", "routeType": {"id": 14, "name": "route by Transaction Type"},
                 "rules": [{"routeRuleId": 1, "enabled": "ENABLED", "isDefaultRule": "NO",
                            "nextRouteOrCascade": {"type": "ROUTE", "routeId": 11, "routeTypeName": "by Card Product"},
                            "criteria": [{"entity": {"id": 1, "name": "sale"}}]},
                           {"routeRuleId": 2, "enabled": "ENABLED", "isDefaultRule": "YES",
                            "nextRouteOrCascade": {"type": "CASCADE", "cascadeId": 7, "cascadeTypeName": "Sequence"},
                            "criteria": []}]}}
CHILD = {"route": {"routeId": 11, "routeType": {"name": "route by Card Product"},
                   "rules": [{"routeRuleId": 3, "enabled": "ENABLED", "isDefaultRule": "NO",
                              "nextRouteOrCascade": {"type": "ROUTE", "routeId": 10},
                              "criteria": [{"value": "MCG"}]}]}}


class Console:
    def __init__(self):
        self.calls = []

    def __call__(self, request, timeout, context):
        path = request.full_url.split("/api", 1)[1]
        body = json.loads(request.data)
        self.calls.append((path, body))
        answers = {
            routing.TOP_ROUTE: TOP, routing.ROUTE: CHILD,
            routing.MERMAID: {"source": "flowchart TB\n A([\"x```y\"]) --> B"},
            "/blueprint/restrictions": {"entityId": 5, "entityName": "BP", "restrictions": [
                {"restrictionId": 54, "restrictionName": "Daily decline limit", "restrictionStatus": "DISABLED",
                 "groupCode": "T"}]},
            "/blueprint/restriction-view": {"entity": {"id": 5}, "restrictionId": 54, "restrictionName": "Daily",
                                            "restrictionStatus": "DISABLED", "parameters": [
                {"parameterId": 255, "parameterName": "Use calendar day", "parameterType": "flag",
                 "parameterValue": "N"}]},
        }
        return json.dumps(answers[path]).encode()


def client(console):
    allowed = routing.PATHS | restrictions.PATHS
    return ConsoleClient(PROFILE, allowed, opener=console, log=CallLog())


class RoutingTest(unittest.TestCase):
    def test_walks_routes_once_and_reads_values(self):
        console = Console()
        rows, source, cut = routing.read(client(console), 3001)
        self.assertEqual([r["route_id"] for r in rows], [10, 10, 11])
        self.assertEqual(rows[0]["values"], ["1"])          # a batch's value is the entity id
        self.assertTrue(rows[1]["default"])
        self.assertEqual(rows[2]["values"], ["MCG"])
        self.assertEqual([p for p, _ in console.calls].count(routing.ROUTE), 1)   # the loop back is not read again
        self.assertFalse(cut)

    def test_mermaid_cannot_close_its_fence(self):
        rows, source, cut = routing.read(client(Console()), 3001)
        text = routing.markdown(3001, rows, source, cut)
        body = text.split("```mermaid", 1)[1]
        self.assertEqual(body.count("```"), 1)              # only the closing fence


class RestrictionsTest(unittest.TestCase):
    def test_list_and_view(self):
        c = client(Console())
        listed = restrictions.list_restrictions(c, "blueprint", 5)
        self.assertEqual(listed["restrictions"][0]["id"], 54)
        view = restrictions.view_restriction(c, "BLUEPRINT", 5, 54)
        self.assertEqual(view["parameters"][0], {"id": 255, "name": "Use calendar day", "type": "flag", "unit": None,
                                                 "value": "N", "description": ""})
        self.assertIn("`param` = the parameter id", restrictions.markdown(view))

    def test_level_checked(self):
        with self.assertRaises(InputError):
            restrictions.list_restrictions(client(Console()), "MERCHANT", 5)


if __name__ == "__main__":
    unittest.main()
