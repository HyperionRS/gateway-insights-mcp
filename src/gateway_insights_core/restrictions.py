# SPDX-License-Identifier: Apache-2.0
"""An entity's restrictions, read from the console: which are attached and how each is set.

The restriction catalogue changes often — new restrictions, new parameters — so nothing about it is
written down here: it is read from the console when asked. Two reads per level, both view rights:

- the list of an entity's restrictions with their status and group;
- one restriction of the entity: description, score, and its parameters — id, name, type, unit and the
  current value. A restriction batch takes the restriction id and the parameter id from here.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .errors import InputError
from .http import ConsoleClient
from .markdown import md_escape, md_table
from .mcp import Tool, ToolResult, schema

LEVELS = {
    "BLUEPRINT": "/blueprint",
    "REQUESTOR": "/requestors",
    "BANK_TERMINAL": "/bank-terminal",
    "PROVIDER": "/provider",
}
PATHS = frozenset(p for base in LEVELS.values() for p in (f"{base}/restrictions", f"{base}/restriction-view"))
DESCRIPTION_CUT = 300


def _level(value: Any) -> str:
    level = str(value or "").strip().upper()
    if level not in LEVELS:
        raise InputError(f"level: one of {', '.join(LEVELS)}")
    return level


def list_restrictions(client: ConsoleClient, level: str, entity_id: int) -> Dict[str, Any]:
    level = _level(level)
    data = client.post(f"{LEVELS[level]}/restrictions", {"entityId": int(entity_id)},
                       what=f"{level.lower().replace('_', ' ')} restrictions")
    rows = [{"id": r.get("restrictionId"), "name": r.get("restrictionName"), "status": r.get("restrictionStatus"),
             "group": r.get("groupCode")} for r in data.get("restrictions") or [] if isinstance(r, dict)]
    return {"level": level, "entity_id": data.get("entityId", entity_id), "entity_name": data.get("entityName"),
            "restrictions": rows}


def view_restriction(client: ConsoleClient, level: str, entity_id: int, restriction_id: int) -> Dict[str, Any]:
    level = _level(level)
    d = client.post(f"{LEVELS[level]}/restriction-view", {"entityId": int(entity_id),
                                                           "restrictionId": int(restriction_id)},
                    what=f"{level.lower().replace('_', ' ')} restriction")
    params = [{"id": p.get("parameterId"), "name": p.get("parameterName"), "type": p.get("parameterType"),
               "unit": p.get("parameterUnit"), "value": p.get("parameterValue"),
               "description": (p.get("parameterDescription") or "")[:DESCRIPTION_CUT]}
              for p in d.get("parameters") or [] if isinstance(p, dict)]
    description = d.get("restrictionDescription") or ""
    return {"level": level, "entity": d.get("entity"), "id": d.get("restrictionId"), "name": d.get("restrictionName"),
            "status": d.get("restrictionStatus"), "description": description[:DESCRIPTION_CUT * 2],
            "score_type": d.get("scoreType"), "score": d.get("scoreValue"), "editable": d.get("editable"),
            "currency": d.get("currency"), "parameters": params,
            "amount_ranges": len(d.get("amountRangeParameters") or [])}


def markdown(data: Dict[str, Any]) -> str:
    if "restrictions" in data:
        head = f"### Restrictions of {data['level'].lower().replace('_', ' ')} {data['entity_id']}"
        if data.get("entity_name"):
            head += f" — {md_escape(data['entity_name'])}"
        if not data["restrictions"]:
            return head + "\n\nNone."
        return head + "\n\n" + md_table(["Id", "Restriction", "Status", "Group"],
                                        [[r["id"], r["name"], r["status"], r["group"]] for r in data["restrictions"]])
    out = [f"### Restriction {data['id']}: {md_escape(data['name'])} — {data['status']}"]
    if data.get("description"):
        out.append(md_escape(data["description"]))
    out.append(f"Score: {data.get('score_type')} {data.get('score')} · editable: {data.get('editable')} · "
               f"currency: {data.get('currency') or '—'}")
    if data["parameters"]:
        out.append(md_table(["Parameter id", "Name", "Type", "Unit", "Current value"],
                            [[p["id"], p["name"], p["type"], p["unit"] or "", p["value"]] for p in data["parameters"]]))
        out.append("A restriction batch takes `restriction` = the restriction id and `param` = the parameter id.")
    if data.get("amount_ranges"):
        out.append(f"{data['amount_ranges']} amount intervals — not changed by batches.")
    return "\n\n".join(out)


def tool(client_for: Callable[[Optional[str]], ConsoleClient], profile_prop: Dict[str, Any]) -> Tool:
    def run(a: Dict[str, Any]) -> ToolResult:
        if a.get("entity_id") in (None, ""):
            raise InputError("entity_id is required; find it with entity_search")
        client = client_for(a.get("profile"))
        if a.get("restriction_id") not in (None, ""):
            data = view_restriction(client, a.get("level"), int(a["entity_id"]), int(a["restriction_id"]))
        else:
            data = list_restrictions(client, a.get("level"), int(a["entity_id"]))
        return ToolResult(markdown(data), data)

    return Tool("entity_restrictions",
                "The restrictions of a blueprint, requestor, bank terminal or provider, read live from the console: "
                "without restriction_id — which are attached and their status; with it — the restriction's "
                "parameters with their ids, types, units and current values (what a restriction batch takes).",
                schema({"profile": profile_prop, "level": {"type": "string", "enum": list(LEVELS)},
                        "entity_id": {"type": "integer"}, "restriction_id": {"type": "integer"}},
                       ["level", "entity_id"]), run, "Entity restrictions")

