# SPDX-License-Identifier: Apache-2.0
"""A blueprint's routing, read from the console: routes, their rules and criteria, and the whole graph.

Two reads, both view rights:

- the routes: the top route, then each route a rule leads to — a route has a type (the criterion it
  routes by, e.g. Transaction Type) and rules; a rule has criterion values (entity id and name, or a
  value or range) and leads to the next route or to a cascade of terminals;
- the graph as Mermaid source, which also shows the terminals of every cascade.

A criterion value of a rule is what a routing batch calls `value`: to add or replace one, read it here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import InputError
from .http import ConsoleClient
from .markdown import md_table
from .mcp import Tool, ToolResult, schema

TOP_ROUTE = "/routing-cascading/top-route"
ROUTE = "/routing-cascading-route/get"
MERMAID = "/routing-cascading/mermaid"
PATHS = frozenset({TOP_ROUTE, ROUTE, MERMAID})
MAX_ROUTES = 20


def _criterion(c: Dict[str, Any]) -> Tuple[str, str]:
    """(value as a batch takes it, how it reads)."""
    entity = c.get("entity") or {}
    if entity.get("id") is not None:
        return str(entity["id"]), f"{entity.get('name') or ''} ({entity['id']})"
    if c.get("value") not in (None, ""):
        return str(c["value"]), str(c["value"])
    for key in ("amountRange", "stringRange"):
        rng = c.get(key)
        if isinstance(rng, dict) and rng:
            text = "…".join(str(rng.get(k)) for k in ("from", "to") if rng.get(k) is not None) or str(rng)
            return text, text
    if c.get("recurringPayment"):
        return str(c["recurringPayment"]), str(c["recurringPayment"])
    return "", "—"


SHOWN_VALUES = 6


def _shown(values: List[Tuple[str, str]], default: bool) -> str:
    """A rule's values as they read; a long list (hundreds of banks) is cut — the full list is in `values`."""
    if not values:
        return "default" if default else "—"
    text = ", ".join(s for _, s in values[:SHOWN_VALUES])
    return text + (f" … +{len(values) - SHOWN_VALUES} more" if len(values) > SHOWN_VALUES else "")


def _route_rows(route: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for rule in route.get("rules") or []:
        nxt = rule.get("nextRouteOrCascade") or {}
        values = [_criterion(c) for c in rule.get("criteria") or [] if isinstance(c, dict)]
        out.append({
            "route_id": route.get("routeId"), "route_type": (route.get("routeType") or {}).get("name"),
            "rule_id": rule.get("routeRuleId"), "enabled": rule.get("enabled"),
            "default": rule.get("isDefaultRule") == "YES",
            "values": [v for v, _ in values], "shown": _shown(values, rule.get("isDefaultRule") == "YES"),
            "next_type": nxt.get("type"), "next_id": nxt.get("routeId") or nxt.get("cascadeId"),
            "next_name": nxt.get("routeTypeName") or nxt.get("cascadeTypeName"),
            "outdated": any(c.get("outdated") for c in rule.get("criteria") or [] if isinstance(c, dict)),
        })
    return out


def read(client: ConsoleClient, blueprint_id: int, *, graph: bool = True,
         max_routes: int = MAX_ROUTES) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    """(rule rows of every route reached, Mermaid source or None, whether the walk was cut short)."""
    blueprint_id = int(blueprint_id)
    top = (client.post(TOP_ROUTE, {"blueprintId": blueprint_id}, what="blueprint top route").get("route") or {})
    rows: List[Dict[str, Any]] = []
    seen, queue, cut = set(), [top], False
    while queue:
        route = queue.pop(0)
        if not route or route.get("routeId") in seen:
            continue
        seen.add(route.get("routeId"))
        route_rows = _route_rows(route)
        rows += route_rows
        for r in route_rows:
            if r["next_type"] == "ROUTE" and r["next_id"] not in seen:
                if len(seen) + len(queue) >= max_routes:
                    cut = True
                    continue
                queue.append(client.post(ROUTE, {"blueprintId": blueprint_id, "routeId": int(r["next_id"])},
                                         what="blueprint route").get("route") or {})
    source = None
    if graph:
        source = client.post(MERMAID, {"blueprintId": blueprint_id}, what="blueprint routing graph").get("source")
    return rows, source, cut


def markdown(blueprint_id: int, rows: List[Dict[str, Any]], source: Optional[str], cut: bool) -> str:
    out = [f"### Routing of blueprint {blueprint_id}"]
    if not rows:
        out.append("The blueprint has no routes.")
    else:
        out.append(md_table(["Route", "Routes by", "Rule", "Values (batch value)", "On", "Leads to"],
                            [[r["route_id"], r["route_type"], r["rule_id"],
                              r["shown"] + (" — outdated" if r["outdated"] else ""), r["enabled"],
                              f"{(r['next_type'] or '').lower()} {r['next_id'] or ''} {r['next_name'] or ''}".strip()]
                             for r in rows]))
        out.append("A routing batch's `value` is the number in brackets (or the value itself).")
    if cut:
        out.append(f"Only the first {MAX_ROUTES} routes were read.")
    if source:
        # The source carries entity names: a fence inside it would end the block and turn data into markdown.
        safe = source.strip().replace("`", "'")
        out.append("**Graph** (terminals of each cascade included)\n\n```mermaid\n" + safe + "\n```")
    return "\n\n".join(out)


def tool(client_for: Callable[[Optional[str]], ConsoleClient], profile_prop: Dict[str, Any]) -> Tool:
    def run(a: Dict[str, Any]) -> ToolResult:
        if a.get("blueprint_id") in (None, ""):
            raise InputError("blueprint_id is required; find it with entity_search")
        rows, source, cut = read(client_for(a.get("profile")), int(a["blueprint_id"]),
                                 graph=a.get("graph", True) is not False)
        return ToolResult(markdown(int(a["blueprint_id"]), rows, source, cut),
                          {"blueprint_id": int(a["blueprint_id"]), "rules": rows, "mermaid": source, "cut": cut})

    return Tool("blueprint_routing",
                "A blueprint's routing: every route, what it routes by, its rules with their criterion values (the "
                "`value` routing batches take), where each rule leads, and the whole graph as Mermaid, terminals of "
                "each cascade included.",
                schema({"profile": profile_prop, "blueprint_id": {"type": "integer"},
                        "graph": {"type": "boolean", "description": "include the Mermaid graph (default true)"}},
                       ["blueprint_id"]), run, "Blueprint routing")

