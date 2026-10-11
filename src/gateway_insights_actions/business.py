# SPDX-License-Identifier: Apache-2.0
"""Business tools: from a finding to a prepared batch task.

Each builds the task's input in the type's own CSV format (or ids, where the type takes no CSV),
prepares it through the raw layer and answers with what was and what will be. None of them runs
anything: a class-1 task is run with batch_run, classes 2 and 3 in the console.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Dict, List

from gateway_insights_core import orders
from gateway_insights_core.errors import InputError
from gateway_insights_core.markdown import md_table
from gateway_insights_core.mcp import Tool, ToolResult, schema

from . import api
from .tools import PROFILE, Context, ContextFn, build_csv, prepare, prepared_structured, prepared_text

MAX_ORDER_PAGES = 5           # order_search pages of 100 behind one task: up to 500 orders


def _answer(ctx: Context, p, title: str) -> ToolResult:
    return ToolResult(prepared_text(ctx, p, title), prepared_structured(ctx, p))


def _rows(a: Dict[str, Any], required: List[str], what: str) -> List[Dict[str, Any]]:
    rows = a.get("rows")
    if not isinstance(rows, list) or not rows:
        raise InputError(f"rows: a list of {what}")
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise InputError(f"rows[{i}] is not an object")
        missing = [k for k in required if r.get(k) in (None, "")]
        if missing:
            raise InputError(f"rows[{i}]: {', '.join(missing)} required")
        out.append(r)
    return out


def _order_ids(ctx: Context, a: Dict[str, Any], today: date) -> List[str]:
    """Ids given, or every order the search finds — up to MAX_ORDER_PAGES pages."""
    if a.get("order_ids"):
        ids = a["order_ids"] if isinstance(a["order_ids"], list) else [a["order_ids"]]
        return [str(int(i)) for i in ids]
    search = a.get("search")
    if not isinstance(search, dict):
        raise InputError("give order_ids, or search with order_search's arguments")
    found: List[str] = []
    args = dict(search, limit=100, offset=0)
    for _ in range(MAX_ORDER_PAGES):
        rows, more, body, _ = orders.search(ctx.api.client, args, today)
        found += [str(r["order_id"]) for r in rows if r.get("order_id") is not None]
        if not more:
            break
        args["offset"] = body["offset"] + body["size"]
    else:
        raise InputError(f"the search finds more than {MAX_ORDER_PAGES * 100} orders; narrow it or split the task")
    if not found:
        raise InputError("the search finds no orders")
    return found


# Types whose payload is `changes` — a list of attribute values applied to every object of the task.
# The generator reports what it stores (D, E, F) and takes what its form sends.
GENERATOR_STORED = {"D": "DISABLED", "E": "ENABLED", "F": "FORCED"}
CHANGES_TYPES = ("REQUESTOR_CONFIG", "REQUESTOR_EAV", "MERCHANT_GENERATOR", "BANK_TERMINAL_PROPERTY")
MAX_REVERT_TASKS = 5


def _revert_changes(ctx: Context, task: Dict[str, Any], changed, note: str) -> ToolResult:
    """The way back for a `changes` type: objects that had the same values before go into one task.

    A value that was absent before («None» — the object inherited it) cannot be set back to absent by a
    batch: those are listed, not prepared."""
    task_type = task["taskType"]
    before: Dict[tuple, Dict[str, str]] = defaultdict(dict)
    absent: List[str] = []
    for it, c in changed:
        # a merchant goes with its manager: the object keeps its sub_key
        obj = (str(it.get("objectId")), str(it.get("subKey") or ""))
        if c.get("beforeValue") is None:
            absent.append(f"{obj[0]} {c.get('field')}")
            continue
        value = str(c.get("beforeValue"))
        if task_type == "MERCHANT_GENERATOR":
            value = GENERATOR_STORED.get(value, value)
        before[obj][str(c.get("field"))] = value
    groups: Dict[str, List[tuple]] = defaultdict(list)
    for obj, values in before.items():
        groups[json.dumps(sorted(values.items()))].append(obj)
    if not groups:
        raise InputError("every changed value was inherited before the run; a batch cannot make it inherited "
                         "again — clear it on the entity's card: " + ", ".join(absent))
    if len(groups) > MAX_REVERT_TASKS:
        raise InputError(f"the objects had {len(groups)} different sets of values before; prepare the way back in "
                         f"parts with batch_preview")
    parts, structured = [], []
    for key, objects in groups.items():
        changes = json.dumps([{"param": k, "value": v} for k, v in json.loads(key)])
        extra = [f"Inherited before the run, not set back by this task: {', '.join(absent)}."] if absent else []
        p = prepare(ctx, task_type, "IDS", objects=[dict({"objectId": o}, **({"subKey": k} if k else {}))
                                                    for o, k in objects],
                    payload={"changes": changes}, note=note, extra_warnings=extra)
        parts.append(prepared_text(ctx, p, f"Way back for task {task['taskId']} — {len(objects)} objects"))
        structured.append(prepared_structured(ctx, p))
        absent = []                                  # said once
    return ToolResult("\n\n".join(parts), {"changed": False, "tasks": structured})


REVERT_PAGE = 100


def _done_items(ctx: Context, task_id: int) -> List[Dict[str, Any]]:
    """Every object a run changed — all pages. A way back for part of a run is not a way back."""
    items: List[Dict[str, Any]] = []
    while True:
        page = ctx.api.items_page(task_id, status="DONE", offset=len(items), size=REVERT_PAGE)
        batch = list(page.get("items") or [])
        items += batch
        if not page.get("hasMore") or not batch:
            return items
        if len(items) >= api.MAX_OBJECTS:
            raise InputError(f"the run changed more than {api.MAX_OBJECTS} objects: prepare the way back in parts "
                             "with batch_preview")


def _status(value: Any) -> str:
    """A status as a batch reads it back (E, D, enabled, disabled) in the form a batch takes."""
    text = str(value or "").strip().upper()
    return {"E": "ENABLED", "D": "DISABLED", "Y": "ENABLED", "N": "DISABLED"}.get(text, text)


ORDER_SOURCE = {
    "order_ids": {"type": "array", "items": {"type": "integer"}, "description": "orders, from order_search"},
    "search": {"type": "object", "description": "order_search arguments: every order it finds goes into the task "
               f"(up to {MAX_ORDER_PAGES * 100})"},
}
NOTE = {"type": "string", "description": "why: the finding this task comes from"}


def build(context: ContextFn, today: Callable[[], date] = date.today) -> List[Tool]:
    def status_check(a):
        ctx = context(a.get("profile"))
        ids = _order_ids(ctx, a, today())
        p = prepare(ctx, "ORDER_QUERY_STATUS", "IDS", objects=[{"objectId": i} for i in ids],
                    payload={"comment": str(a.get("comment") or "status check")}, note=a.get("note") or "")
        return _answer(ctx, p, "Ask providers for the status again")

    def order_action(a):
        ctx = context(a.get("profile"))
        task_type = str(a.get("task_type") or "").upper()
        if not task_type.startswith("ORDER_"):
            raise InputError("task_type: an ORDER_* batch type; see batch_types object_type=ORDER")
        ids = _order_ids(ctx, a, today())
        p = prepare(ctx, task_type, "IDS", objects=[{"objectId": i} for i in ids], payload=a.get("payload"),
                    note=a.get("note") or "")
        return _answer(ctx, p, f"Order operation {task_type}")

    def disable(a):
        ctx = context(a.get("profile"))
        ids = a.get("requestor_ids") or []
        if not ids:
            raise InputError("requestor_ids: the requestors to switch, from entity_search")
        value = "ENABLED" if a.get("enable") else "DISABLED"
        p = prepare(ctx, "ENTITY_ENABLE", "IDS", objects=[{"objectId": str(int(i))} for i in ids],
                    payload={"value": value}, note=a.get("note") or "")
        return _answer(ctx, p, f"Switch requestors {'on' if a.get('enable') else 'off'}")

    def route_status(a):
        ctx = context(a.get("profile"))
        ids = a.get("bank_terminal_ids") or []
        if not ids:
            raise InputError("bank_terminal_ids: the terminals, from entity_search")
        status = "ENABLED" if a.get("enable") else "DISABLED"
        text = build_csv(["bank_terminal_id", "status"], [{"bank_terminal_id": int(i), "status": status} for i in ids])
        p = prepare(ctx, "BANK_TERMINAL_ROUTING_STATUS", "CSV", csv_text=text, note=a.get("note") or "")
        return _answer(ctx, p, f"Switch terminals {'into' if a.get('enable') else 'out of'} routing")

    def reroute(a):
        ctx = context(a.get("profile"))
        mode = str(a.get("mode") or "").upper()
        if mode == "REPLACE_TERMINAL":
            rows = _rows(a, ["bank_terminal_id", "new_bank_terminal_id"], "{bank_terminal_id, new_bank_terminal_id}")
            text = build_csv(["bank_terminal_id", "new_bank_terminal_id"], rows)
            p = prepare(ctx, "BANK_TERMINAL_ROUTING_REPLACE", "CSV", csv_text=text, note=a.get("note") or "")
        elif mode in ("ADD_CRITERION_VALUE", "REPLACE_CRITERION_VALUE"):
            rows = _rows(a, ["blueprint_id", "criterion", "value"], "{blueprint_id, criterion, value, new_value}")
            text = build_csv(["blueprint_id", "criterion", "value", "new_value"], rows)
            task_type = "ROUTING_CRITERION_ADD_VALUE" if mode.startswith("ADD") else "ROUTING_CRITERION_REPLACE_VALUE"
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=a.get("note") or "")
        else:
            raise InputError("mode: REPLACE_TERMINAL, ADD_CRITERION_VALUE or REPLACE_CRITERION_VALUE")
        return _answer(ctx, p, "Change routing")

    def terminal_pool(a):
        ctx = context(a.get("profile"))
        replace = bool(a.get("replace"))
        rows = _rows(a, ["bank_terminal_id"], "{bank_terminal_id, provider_id, name, descriptor}")
        text = build_csv(["bank_terminal_id", "provider_id", "name", "descriptor"], rows)
        payload = {"enabled": "ENABLED" if a.get("enabled", replace) else "DISABLED"}
        task_type = "BANK_TERMINAL_CLONE_REPLACE" if replace else "BANK_TERMINAL_CLONE_EXTEND"
        p = prepare(ctx, task_type, "CSV", csv_text=text, payload=payload, note=a.get("note") or "")
        return _answer(ctx, p, "Replace terminals with new ones" if replace else "Add terminal copies to the pool")

    def restriction(a):
        ctx = context(a.get("profile"))
        rows = _rows(a, ["level", "entity_id", "restriction_id", "parameter", "new_value"],
                     "{level, entity_id, restriction_id, parameter, old_value, new_value}")
        text = build_csv(["level", "entity_id", "restriction_id", "parameter", "old_value", "new_value"], rows)
        p = prepare(ctx, "RESTRICTION_PARAM", "CSV", csv_text=text, note=a.get("note") or "")
        return _answer(ctx, p, "Change restriction parameters")

    def level_rows(task_type: str, title: str):
        def handler(a):
            ctx = context(a.get("profile"))
            rows = _rows(a, ["level", "entity_id", "field", "new_value"],
                         "{level, entity_id, field, new_value, old_value}")
            text = build_csv(["level", "entity_id", "field", "new_value", "old_value"], rows)
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=a.get("note") or "")
            return _answer(ctx, p, title)
        return handler

    def api_host(a):
        ctx = context(a.get("profile"))
        rows = _rows(a, ["requestor_id", "from_hosts", "to_host"], "{requestor_id, from_hosts, to_host}")
        text = build_csv(["requestor_id", "from_hosts", "to_host"], rows)
        p = prepare(ctx, "REQUESTOR_API_HOST", "CSV", csv_text=text, note=a.get("note") or "")
        return _answer(ctx, p, "Change requestors' API host")

    def secret(a):
        ctx = context(a.get("profile"))
        ids = a.get("bank_terminal_ids") or []
        if not ids:
            raise InputError("bank_terminal_ids: the terminals, from entity_search")
        payload = {"param": str(a.get("param") or ""), "secret": str(a.get("secret") or "")}
        p = prepare(ctx, "BANK_TERMINAL_PSP_SECRET", "IDS", objects=[{"objectId": str(int(i))} for i in ids],
                    payload=payload, secret_field="secret", secret_ref=a.get("secret_ref"),
                    note=a.get("note") or "")
        return _answer(ctx, p, "Set the PSP password on terminals")

    def revert(a):
        ctx = context(a.get("profile"))
        task = ctx.api.view(int(a.get("task_id")))
        task_type = str(task.get("taskType") or "")
        if task.get("mode") != "RUN" or task.get("status") not in ("DONE", "PARTIAL"):
            raise InputError(f"task {task.get('taskId')} is {task.get('mode')} {task.get('status')}: only a run that "
                             "changed something (RUN, DONE or PARTIAL) has a way back")
        items = _done_items(ctx, task["taskId"])
        changed = [(it, c) for it in items for c in (it.get("changes") or []) if c.get("changed")]
        if not changed:
            raise InputError("the run changed nothing that can be read back")
        note = f"revert of task {task['taskId']}"
        if task_type in ("ENTITY_NAME", "ENTITY_RATE_PLAN"):
            rows = []
            for it, c in changed:
                level, _, entity_id = str(it.get("objectId") or "").partition(":")
                before, after = c.get("beforeValue") or "", c.get("afterValue") or ""
                if task_type == "ENTITY_RATE_PLAN":       # the console reports «43:Plan name»; a batch takes 43
                    before, after = before.split(":", 1)[0], after.split(":", 1)[0]
                rows.append({"level": level, "entity_id": entity_id, "field": it.get("subKey") or c.get("field"),
                             "new_value": before, "old_value": after})
            text = build_csv(["level", "entity_id", "field", "new_value", "old_value"], rows)
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note)
        elif task_type == "BANK_TERMINAL_ROUTING_STATUS":
            # The run reports «routing: enabled → disabled»; the CSV takes ENABLED / DISABLED.
            text = build_csv(["bank_terminal_id", "status"],
                             [{"bank_terminal_id": it.get("objectId"), "status": _status(c.get("beforeValue"))}
                              for it, c in changed if c.get("field") == "routing"])
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note)
        elif task_type == "ENTITY_ENABLE":
            groups: Dict[str, List[str]] = defaultdict(list)
            for it, c in changed:      # the run reports «status_name: E → D»
                groups[_status(c.get("beforeValue"))].append(str(it.get("objectId")))
            if len(groups) != 1:
                raise InputError("the objects had different states before: revert them with plan_disable in groups: "
                                 + "; ".join(f"{k or 'empty'}: {', '.join(v)}" for k, v in groups.items()))
            (before, ids), = groups.items()
            p = prepare(ctx, task_type, "IDS", objects=[{"objectId": i} for i in ids], payload={"value": before},
                        note=note)
        elif task_type == "REQUESTOR_API_HOST":
            text = build_csv(["requestor_id", "from_hosts", "to_host"],
                             [{"requestor_id": it.get("objectId"), "from_hosts": c.get("afterValue"),
                               "to_host": c.get("beforeValue")} for it, c in changed])
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note)
        elif task_type == "BANK_TERMINAL_PROPERTY_CSV":
            # «terminal_location: PREMISES -> EMPTY»: a CSV of the same property with the earlier value.
            fields = sorted({str(c.get("field")) for _, c in changed})
            if len(fields) != 1 or any(c.get("beforeValue") is None for _, c in changed):
                raise InputError("the run changed several properties or an inherited one; prepare the way back with "
                                 "batch_preview")
            text = build_csv(["bank_terminal_id", fields[0]],
                             [{"bank_terminal_id": it.get("objectId"), fields[0]: c.get("beforeValue")}
                              for it, c in changed])
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note)
        elif task_type == "ROUTING_CRITERION_REPLACE_VALUE":
            # «transaction_type: 19 -> 18» on a blueprint: replace 18 back with 19.
            text = build_csv(["blueprint_id", "criterion", "value", "new_value"],
                             [{"blueprint_id": it.get("objectId"), "criterion": str(c.get("field") or "").upper(),
                               "value": c.get("afterValue"), "new_value": c.get("beforeValue")} for it, c in changed])
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note)
        elif task_type == "RESTRICTION_PARAM":
            # objectId «BLUEPRINT:9», subKey «36/skip unapproved transactions», change of that parameter.
            rows, attached = [], []
            for it, c in changed:
                if c.get("field") == "restriction":      # attaching a restriction has no way back by batch
                    attached.append(str(it.get("objectId")))
                    continue
                level, _, entity_id = str(it.get("objectId") or "").partition(":")
                restriction_id, _, parameter = str(it.get("subKey") or "").partition("/")
                rows.append({"level": level, "entity_id": entity_id, "restriction_id": restriction_id,
                             "parameter": parameter, "old_value": c.get("afterValue") or "",
                             "new_value": c.get("beforeValue") or ""})
            if not rows:
                raise InputError("the run only attached restrictions; a batch does not detach them")
            text = build_csv(["level", "entity_id", "restriction_id", "parameter", "old_value", "new_value"], rows)
            extra = [f"The run also attached the restriction to {', '.join(attached)}; a batch does not detach it — "
                     "do that on the entity's card."] if attached else []
            p = prepare(ctx, task_type, "CSV", csv_text=text, note=note, extra_warnings=extra)
        elif task_type in CHANGES_TYPES:
            return _revert_changes(ctx, task, changed, note)
        else:
            rows = [[it.get("objectId"), c.get("field"), c.get("beforeValue"), c.get("afterValue")] for it, c in changed]
            return ToolResult(f"### No automatic way back for {task_type}\n\nNothing was prepared. What the run "
                              "changed — prepare the way back with batch_preview:\n\n"
                              + md_table(["Object", "Field", "Was", "Now"], rows[:50]),
                              {"changed": False, "task": task, "changes": rows})
        return _answer(ctx, p, f"Way back for task {task['taskId']}")

    rows_schema = {"type": "array", "items": {"type": "object"}}
    base = {"profile": PROFILE, "note": NOTE}
    return [
        Tool("plan_status_check", "Prepare asking providers for the status of orders again (class 1): orders "
             "stuck in UNKNOWN or PROCESSING, from order_search or ids.",
             schema({**base, **ORDER_SOURCE, "comment": {"type": "string"}}), status_check,
             "Plan a status check", effect="prepare"),
        Tool("plan_order_action", "Prepare an order operation on many orders — reversal, capture, void, cancel, "
             "chargeback, fraud, resolve, approve, decline… (class 2): run by a person in the console. Payload as "
             "batch_fields shows for the type.",
             schema({**base, **ORDER_SOURCE, "task_type": {"type": "string"},
                     "payload": {"type": "object", "additionalProperties": {"type": "string"}}}, ["task_type"]),
             order_action, "Plan an order operation", effect="prepare"),
        Tool("plan_disable", "Prepare switching requestors off — or on with enable=true (class 1): merchants gone "
             "quiet, endpoints to retire.",
             schema({**base, "requestor_ids": {"type": "array", "items": {"type": "integer"}},
                     "enable": {"type": "boolean"}}, ["requestor_ids"]), disable,
             "Plan switching requestors", effect="prepare"),
        Tool("plan_route_status", "Prepare switching bank terminals out of routing (class 1) — or into it with "
             "enable=true (class 2, run in the console): a terminal that fails every payment, a route to drain.",
             schema({**base, "bank_terminal_ids": {"type": "array", "items": {"type": "integer"}},
                     "enable": {"type": "boolean"}}, ["bank_terminal_ids"]), route_status,
             "Plan a routing switch", effect="prepare"),
        Tool("plan_reroute", "Prepare a routing change from a finding such as decline_route: replace a terminal in "
             "routing with an existing one (class 2), add a value of a routing criterion on blueprints (class 2: a "
             "batch cannot remove it again), or replace one (class 1). rows: {bank_terminal_id, new_bank_terminal_id} or {blueprint_id, criterion, value, new_value}.",
             schema({**base, "mode": {"type": "string", "enum": ["REPLACE_TERMINAL", "ADD_CRITERION_VALUE",
                                                                   "REPLACE_CRITERION_VALUE"]},
                     "rows": rows_schema}, ["mode", "rows"]), reroute, "Plan a reroute", effect="prepare"),
        Tool("plan_terminal_pool", "Prepare copies of bank terminals: added to the pool with routing rows off "
             "(class 1), or replacing the original (replace=true, class 2). A credential column makes it class 3. "
             "rows: {bank_terminal_id, provider_id, name, descriptor}.",
             schema({**base, "rows": rows_schema, "replace": {"type": "boolean"}, "enabled": {"type": "boolean"}},
                    ["rows"]), terminal_pool, "Plan terminal copies", effect="prepare"),
        Tool("plan_restriction", "Prepare restriction parameter changes (class 1) on any level, from findings such "
             "as decline_where. rows: {level, entity_id, restriction_id, parameter, old_value, new_value}.",
             schema({**base, "rows": rows_schema}, ["rows"]), restriction, "Plan restriction changes",
             effect="prepare"),
        Tool("plan_rename", "Prepare names or descriptions of entities (class 1). rows: {level, entity_id, field, "
             "new_value, old_value}.",
             schema({**base, "rows": rows_schema}, ["rows"]), level_rows("ENTITY_NAME", "Rename entities"),
             "Plan renames", effect="prepare"),
        Tool("plan_rate_plan", "Prepare the manager or reseller tariff of blueprints, requestors or terminals "
             "(class 1), from findings such as ceo_earnings. rows: {level, entity_id, field (mrtp or rrtp), "
             "new_value (a tariff id; inherit for a requestor), old_value}.",
             schema({**base, "rows": rows_schema}, ["rows"]), level_rows("ENTITY_RATE_PLAN", "Change tariffs"),
             "Plan tariff changes", effect="prepare"),
        Tool("plan_api_host", "Prepare moving requestors' tokens to another API host (class 1). rows: "
             "{requestor_id, from_hosts, to_host}.",
             schema({**base, "rows": rows_schema}, ["rows"]), api_host, "Plan an API host change",
             effect="prepare"),
        Tool("plan_secret", "Prepare setting the PSP password on bank terminals (class 3): the value comes from "
             "secret_ref (source:name of the profile's secret_sources) and never passes through the conversation; "
             "a person runs it in the console. param — the terminal property holding the password.",
             schema({**base, "bank_terminal_ids": {"type": "array", "items": {"type": "integer"}},
                     "param": {"type": "string"}, "secret_ref": {"type": "string"},
                     "secret": {"type": "string", "description": "only if there is no source: the value as text — "
                                "it then counts as exposed"}}, ["bank_terminal_ids"]),
             secret, "Plan a password change", effect="prepare"),
        Tool("plan_revert", "Prepare the way back for a run: the same type with what was before (class as the "
             "type). Covers names and descriptions, tariffs, routing switches, requestor switches, API hosts, "
             "routing criterion replacements, restriction parameters, and attribute changes (requestor configs and "
             "attributes, merchant generator, terminal properties, terminal properties from CSV); for other types it "
             "lists what changed.",
             schema({"profile": PROFILE, "task_id": {"type": "integer"}}, ["task_id"]), revert,
             "Plan a way back", effect="prepare"),
    ]

