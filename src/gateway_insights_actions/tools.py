# SPDX-License-Identifier: Apache-2.0
"""Tools of the actions server.

Reading tools find objects and read tasks. Preparing tools register a batch preview — nothing in the
console changes but the task itself. One writing tool, batch_run, runs a prepared task of class 1;
tasks of classes 2 and 3 are run by a person in the console, and no tool here can run them.

Business tools (plan_*) turn a finding into a prepared task. They are built only from the raw layer
(ActionsApi and the shared searches) and build their input as CSV in the type's own format, so a task
reads the same in the console as one a person would load.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from gateway_insights_core import entities, orders, restrictions, routing, secrets
from gateway_insights_core.business import provenance_of
from gateway_insights_core.config import Profile
from gateway_insights_core.errors import ApiError, InputError
from gateway_insights_core.markdown import md_escape, md_table
from gateway_insights_core.mcp import Tool, ToolResult, schema

from . import api, classes, guard, guides, journal
from .api import ActionsApi

PROFILE = {"type": "string", "description": "configuration profile; default: the configured default"}
Asker = Callable[[str, Dict[str, Any], Optional[List[str]]], Optional[Dict[str, Any]]]


@dataclass
class Context:
    api: ActionsApi
    today: date
    ask: Asker

    @property
    def profile(self) -> Profile:
        return self.api.client.profile


ContextFn = Callable[[Optional[str]], Context]


# ---- shared pieces ------------------------------------------------------------------------------

def console_ui(profile: Profile) -> str:
    """Where the console opens in a browser: console_ui_url, else guessed from the API address —
    an API at …/console-api/api serves its screens at …/console."""
    if profile.console_ui_url:
        return profile.console_ui_url
    parts = urlsplit(profile.base_url)
    path = parts.path
    head = path.split("/console-api", 1)[0] + "/console" if "/console-api" in path else ""
    return f"{parts.scheme}://{parts.netloc}{head}"


def task_link(profile: Profile, task_id: Any) -> str:
    return f"{console_ui(profile)}/batch-operations/tasks/{task_id}"


def counts_line(task: Dict[str, Any]) -> str:
    c = task.get("counts") or {}
    return (f"{c.get('items', 0)} objects: {c.get('done', 0)} done, {c.get('failed', 0)} failed, "
            f"{c.get('skipped', 0)} skipped, {c.get('attention', 0)} need attention, {c.get('pending', 0)} pending")


def provenance(ctx: Context) -> List[Dict[str, str]]:
    return provenance_of(ctx.api.client.log)


def how_obtained(ctx: Context) -> str:
    """The calls behind the answer; a poll repeating the same read is one line with its count."""
    lines: List[str] = []
    last, repeat = None, 0
    for p in provenance(ctx) + [None]:
        key = (p["path"], p["params"]) if p else None
        if key == last:
            repeat += 1
            continue
        if last is not None:
            lines.append(f"- `POST {last[0]}` `{last[1].replace('`', chr(39))}`" + (f" ×{repeat}" if repeat > 1 else ""))
        last, repeat = key, 1
    return "**How obtained**\n" + "\n".join(lines) if lines else ""


def build_csv(header: List[str], rows: List[Dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    for r in rows:
        writer.writerow(["" if r.get(h) is None else str(r.get(h)) for h in header])
    return out.getvalue()


def _items_table(items: List[Dict[str, Any]]) -> str:
    rows = []
    for it in items:
        changes = [c for c in (it.get("changes") or []) if isinstance(c, dict)]
        shown = "; ".join(f"{c.get('field')}: {c.get('beforeValue') or '—'} → {c.get('afterValue') or '—'}"
                          + ("" if c.get("changed", True) else " (same)") for c in changes[:4])
        if len(changes) > 4:
            shown += f"; +{len(changes) - 4} more"
        rows.append([it.get("objectId"), (it.get("subKey") or ""), it.get("status"),
                     it.get("errorCode") or "", shown or "—"])
    return md_table(["Object", "Part", "Status", "Refusal", "Was → will be"], rows)


def _reasons_table(reasons: List[Dict[str, Any]]) -> str:
    rows = [[r.get("status"), r.get("errorCode") or "—", r.get("itemCount"), r.get("sampleRefs") or ""]
            for r in reasons if r.get("errorCode") or r.get("status") not in ("DONE",)]
    return md_table(["Status", "Code", "Objects", "Examples"], rows) if rows else ""


@dataclass
class Prepared:
    task: Dict[str, Any]
    klass: int
    duplicate: bool
    key: str
    warnings: List[str]
    items: List[Dict[str, Any]]
    reasons: List[Dict[str, Any]]
    secret_refs: List[str]


def prepare(ctx: Context, task_type: str, source: str, *, objects=None, selection=None, csv_text=None,
            payload=None, secret_field: Optional[str] = None, secret_ref: Optional[str] = None,
            sensitive: bool = False, note: str = "", extra_warnings: Optional[List[str]] = None) -> Prepared:
    """Register a preview, wait for it, read its first items and refusals, journal it."""
    task_type = str(task_type or "").strip().upper()
    if not task_type:
        raise InputError("task_type is required")
    payload = {str(k): "" if v is None else str(v) for k, v in (payload or {}).items()}
    warnings = list(extra_warnings or [])
    refs: List[str] = []
    literal = False
    if secret_field:
        if secret_ref:
            resolved = secrets.resolve(secret_ref, ctx.profile.secret_sources)
            payload[secret_field] = resolved.value
            refs.append(f"{resolved.reference} {resolved.fingerprint}")
        elif payload.get(secret_field):
            literal = True
        else:
            raise InputError(f"{secret_field}: give secret_ref (source:name from the profile's secret_sources)")
    request = {"payload": {k: v for k, v in payload.items() if k != secret_field}, "csv": csv_text,
               "objects": objects}
    if literal or guard.carries_secret({k: v for k, v in request.items() if v}):
        warnings.append(secrets.ROTATE_WARNING)

    klass = classes.task_class(task_type, sensitive=sensitive or bool(secret_field), payload=payload,
                               csv_text=csv_text, objects=objects)
    task, duplicate, key = ctx.api.register_preview(task_type, source, objects=objects, selection=selection,
                                                    csv_text=csv_text, payload=payload, secret_field=secret_field)
    task_id = task.get("taskId")
    if task_id is not None and task.get("status") not in api.SETTLED:
        task = ctx.api.wait(task_id)
    items: List[Dict[str, Any]] = []
    reasons: List[Dict[str, Any]] = []
    if task_id is not None and task.get("status") in api.SETTLED:
        items = list(ctx.api.items_page(task_id, size=20).get("items") or [])
        reasons = ctx.api.reasons(task_id)

    c = task.get("counts") or {}
    total = int(c.get("items") or 0)
    limit = ctx.profile.warn_orders if task.get("objectType") == "ORDER" else ctx.profile.warn_entities
    if total > limit:
        kind = "orders" if task.get("objectType") == "ORDER" else "objects"
        warnings.append(f"{total} {kind} in one task — above this profile's warning threshold of {limit}.")
    refused = int(c.get("failed") or 0) + int(c.get("skipped") or 0) + int(c.get("attention") or 0)
    if total and refused / total > 0.2:
        warnings.append(f"{refused} of {total} objects are refused in the preview; see the refusals below.")
    if duplicate:
        warnings.append("The same request was prepared minutes ago: this is that task and its snapshot, not a new one.")

    journal.record(ctx.profile.report_dir, "preview", profile=ctx.profile.name, console=ctx.profile.base_url,
                   task=task, klass=klass,
                   idempotency_key=key, provenance=provenance(ctx), secret_refs=refs, note=note)
    return Prepared(task, klass, duplicate, key, warnings, items, reasons, refs)


def prepared_text(ctx: Context, p: Prepared, title: str) -> str:
    task = p.task
    task_id = task.get("taskId")
    status = task.get("status")
    link = task_link(ctx.profile, task_id)
    if status == "PREVIEWED":
        if classes.runs_here(p.klass):
            head = (f"**PREPARED — nothing changed yet.** Task {task_id} ({task.get('displayName') or task.get('taskType')}), "
                    f"class 1 (reversible): run it with batch_run task_id={task_id}, or in the console: {link}")
        else:
            head = (f"**PREPARED — nothing changed yet.** Task {task_id} ({task.get('displayName') or task.get('taskType')}), "
                    f"class {p.klass} ({classes.NAMES[p.klass]}): **check it and run it in the console** — this "
                    f"server does not run it: {link}")
    elif status in api.SETTLED:
        head = f"**Preview ended as {status}.** Task {task_id}: {task.get('errorCode') or ''} " \
               f"{task.get('errorMessage') or ''} {link}".strip()
    else:
        head = (f"**PREPARING — nothing changed yet.** Task {task_id} is still being checked ({status}); read it "
                f"with batch_task task_id={task_id}, or in the console: {link}")
    out = [f"### {md_escape(title)}", head, counts_line(task)]
    out += [f"⚠ {md_escape(w)}" for w in p.warnings]
    if p.items:
        out.append(_items_table(p.items))
    reasons = _reasons_table(p.reasons)
    if reasons:
        out.append("**Refusals**\n\n" + reasons)
    if p.secret_refs:
        out.append("Secrets from: " + ", ".join(md_escape(r) for r in p.secret_refs))
    out.append(how_obtained(ctx))
    return "\n\n".join(x for x in out if x)


def prepared_structured(ctx: Context, p: Prepared) -> Dict[str, Any]:
    return {"changed": False, "task": p.task, "class": p.klass, "runs_here": classes.runs_here(p.klass),
            "console_link": task_link(ctx.profile, p.task.get("taskId")), "idempotency_key": p.key,
            "duplicate": p.duplicate, "warnings": p.warnings, "items": p.items, "reasons": p.reasons,
            "secret_refs": p.secret_refs}


def _objects(args: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    raw = args.get("objects")
    ids = args.get("ids")
    out: List[Dict[str, Any]] = []
    if ids:
        if not isinstance(ids, list):
            ids = [ids]
        out += [{"objectId": str(i)} for i in ids]
    if raw:
        if not isinstance(raw, list):
            raise InputError("objects must be a list of {id, sub_key, params}")
        for o in raw:
            if not isinstance(o, dict) or o.get("id") in (None, ""):
                raise InputError("every object needs an id")
            item: Dict[str, Any] = {"objectId": str(o["id"])}
            if o.get("sub_key"):
                item["subKey"] = str(o["sub_key"])
            if o.get("params"):
                item["params"] = {str(k): str(v) for k, v in dict(o["params"]).items()}
            out.append(item)
    return out or None


def _selection(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sel = args.get("selection")
    if not sel:
        return None
    if not isinstance(sel, dict):
        raise InputError("selection is an object: level, query, status and *_ids filters")
    body: Dict[str, Any] = {"level": api.choice(sel.get("level"), api.LEVELS, "selection.level"),
                            "searchFilters": api.search_filters(sel)}
    if sel.get("query"):
        body["searchText"] = str(sel["query"])[:100]
    if sel.get("status"):
        body["status"] = api.choice(sel["status"], ("ENABLED", "DISABLED"), "selection.status")
    return body


def _check_task(ctx: Context, task_id: Any) -> Dict[str, Any]:
    try:
        return ctx.api.view(int(task_id))
    except (TypeError, ValueError):
        raise InputError("task_id must be an integer") from None


# ---- raw tools -----------------------------------------------------------------------------------

ID_LIST = {"type": "array", "items": {"type": ["integer", "string"]}}
FILTER_PROPS = {k: {"type": "array", "items": {"type": "integer"}, "description": f"only these {v.lower()} ids"}
                for k, v in api.FILTERS.items()}
SELECTION = {"type": "object", "description": "pick entities by filter instead of ids: level (BANK_TERMINAL, "
             "BLUEPRINT, REQUESTOR, MERCHANT), query, status, and *_ids filters as in entity_search",
             "properties": {"level": {"type": "string", "enum": list(api.LEVELS)}, "query": {"type": "string"},
                            "status": {"type": "string", "enum": ["ENABLED", "DISABLED"]}, **FILTER_PROPS}}
OBJECTS = {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": ["integer", "string"]}, "sub_key": {"type": "string"},
    "params": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["id"]},
    "description": "objects with their own parameters; use ids when they have none"}


def build(context: ContextFn) -> List[Tool]:
    def check(a):
        ctx = context(a.get("profile"))
        p = ctx.profile
        lines = [f"- profile: {md_escape(p.name)} → {md_escape(p.base_url)}",
                 f"- token: {md_escape(p.token_hint)}", f"- TLS verification: {'on' if p.verify_tls else 'OFF'}",
                 f"- console links: {md_escape(task_link(p, '…'))}",
                 f"- journal: {md_escape(journal.journal_path(p.report_dir))}",
                 f"- warning thresholds: {p.warn_orders} orders, {p.warn_entities} entities",
                 f"- secret sources: {md_escape(', '.join(sorted(p.secret_sources)) or 'none')}",
                 f"- customer data in order search: {'shown' if p.show_customer_data else 'hidden'}"]
        try:
            types = ctx.api.types()
            by_class: Dict[int, int] = {}
            for t in types:
                k = classes.type_class(str(t.get("taskType")), bool(t.get("sensitive")))
                by_class[k] = by_class.get(k, 0) + 1
            lines.append(f"- batch types this token sees: {len(types)} (class 1: {by_class.get(1, 0)}, "
                         f"class 2: {by_class.get(2, 0)}, class 3: {by_class.get(3, 0)})")
            waiting = ctx.api.list_counts("MINE").get("previewed")
            lines.append(f"- your prepared tasks waiting for a run: {waiting}")
        except Exception as exc:     # the check reports, it does not fail
            lines.append(f"- batch probe: {md_escape(exc)}")
        text = ("### Connection check — this server CHANGES DATA in the console above\n\n" + "\n".join(lines))
        return ToolResult(text)

    def types_tool(a):
        ctx = context(a.get("profile"))
        types = ctx.api.types(a.get("object_type"))
        rows, out = [], []
        for t in types:
            k = classes.type_class(str(t.get("taskType")), bool(t.get("sensitive")))
            dyn = t.get("taskType") in classes.ROUTING_ON or t.get("taskType") in classes.CREDENTIALS
            rows.append([t.get("taskType"), t.get("displayName"), t.get("objectType"),
                         ",".join(t.get("sources") or []), f"{k}{' (by task)' if dyn else ''}", t.get("maxItems")])
            out.append({**t, "class": k, "class_by_task": dyn})
        text = "### Batch types\n\n" + md_table(["Type", "Name", "Object", "Sources", "Class", "Max"], rows)
        text += ("\n\nClass 1 — this server runs it after a preview; 2 and 3 — prepared here, run by a person in "
                 "the console. «By task» — the class rises when the task switches routing on or carries a "
                 "credential.")
        return ToolResult(text, {"types": out})

    def fields_tool(a):
        ctx = context(a.get("profile"))
        task_type = str(a.get("task_type") or "").upper()
        types = {t.get("taskType"): t for t in ctx.api.types()}
        t = types.get(task_type)
        if t is None:
            raise InputError(f"task_type {task_type!r} is not available to this token; see batch_types")
        has_changes = any(f.get("type") == "changes" for f in t.get("payloadSchema") or [])
        # Only a type with a `changes` field has an attribute form; the console refuses the others.
        data: Dict[str, Any] = {}
        if has_changes:
            try:
                data = ctx.api.fields(task_type, a.get("provider_id"))
            except ApiError as exc:      # some types keep their form outside the metadata (the generator)
                if exc.status != 400:
                    raise
        rows = []
        for g in data.get("groups") or []:
            for attr in g.get("attributes") or []:
                rows.append([g.get("groupName") or g.get("groupKey"), attr.get("key"), attr.get("name"),
                             ", ".join(attr.get("enumValues") or [])[:120], "yes" if attr.get("mask") else ""])
        schema_rows = [[f.get("key"), f.get("type"), f.get("label"), f.get("defaultValue") or "", f.get("dict") or ""]
                       for f in t.get("payloadSchema") or []]
        text = f"### Fields of {md_escape(task_type)}"
        if schema_rows:
            text += "\n\n**Payload**\n\n" + md_table(["Key", "Type", "Label", "Default", "Dictionary"], schema_rows)
        if t.get("csvHeader"):
            text += f"\n\nCSV header: `{t['csvHeader']}`"
        if rows:
            text += "\n\n**Attributes for `changes`** (payload changes = JSON [{\"param\": key, \"value\": …}])\n\n"
            text += md_table(["Group", "Key", "Name", "Values", "Masked"], rows)
        if data.get("locked"):
            text += "\n\nLocked: " + ", ".join(f"{md_escape(k)} ({md_escape(v)})" for k, v in data["locked"].items())
        if data.get("needsProvider"):
            text += "\n\nThe attribute set depends on the provider: pass provider_id."
        guide = guides.guide_text(task_type)
        if guide:
            text += "\n\n" + guide
        return ToolResult(text, {"type": t, "fields": data})

    def dict_tool(a):
        ctx = context(a.get("profile"))
        name = str(a.get("dictionary") or "")
        data = ctx.api.dictionary(name, a.get("query") or "", a.get("manager_id") or a.get("reseller_id"))
        items = [{"value": i.get("code") if i.get("code") is not None else i.get("id"), "name": i.get("name")}
                 for i in data.get("items") or [] if isinstance(i, dict)]
        text = f"### {name.replace('_', ' ').capitalize()}\n\n" + (
            md_table(["Value", "Name"], [[i["value"], i["name"]] for i in items]) if items else "Nothing found.")
        if data.get("hasMore"):
            text += "\n\nMore entries match: narrow the query."
        return ToolResult(text, {"dictionary": name, "items": items})

    def hosts_tool(a):
        ctx = context(a.get("profile"))
        manager = a.get("manager_id")
        hosts = ctx.api.hosts(None if manager in (None, "") else int(manager), a.get("query") or "")
        if a.get("api_only", True):
            hosts = [h for h in hosts if h.get("isApiHost")]
        rows = [{"id": h.get("id"), "name": h.get("name"), "api": bool(h.get("isApiHost")),
                 "redirect": bool(h.get("isRedirectHost")),
                 "managers": [m.get("name") for m in h.get("managers") or [] if isinstance(m, dict)]} for h in hosts]
        text = "### Hosts" + (f" of manager {manager}" if manager else "") + "\n\n"
        text += (md_table(["Id", "Host", "API", "Redirect", "Managers"],
                          [[r["id"], r["name"], "yes" if r["api"] else "", "yes" if r["redirect"] else "",
                            ", ".join(r["managers"]) or "—"] for r in rows]) if rows else "None.")
        text += "\n\nA requestor's API host may only be moved to an API host of its manager."
        return ToolResult(text, {"hosts": rows})

    def count_tool(a):
        ctx = context(a.get("profile"))
        selection = _selection({"selection": a})
        n = ctx.api.objects_count(selection)
        return ToolResult(f"### Selection\n\n{n} {selection['level'].lower().replace('_', ' ')} objects match.",
                          {"count": n, "selection": selection})

    def preview_tool(a):
        ctx = context(a.get("profile"))
        objects = _objects(a)
        selection = _selection(a)
        csv_text = a.get("csv")
        source = "CSV" if csv_text else "SELECTION" if selection else "IDS"
        types = {t.get("taskType"): t for t in ctx.api.types()}
        t = types.get(str(a.get("task_type") or "").upper())
        if t is None:
            raise InputError(f"task_type {a.get('task_type')!r} is not available to this token; see batch_types")
        p = prepare(ctx, t["taskType"], source, objects=objects, selection=selection, csv_text=csv_text,
                    payload=a.get("payload"), secret_field=a.get("secret_field"), secret_ref=a.get("secret_ref"),
                    sensitive=bool(t.get("sensitive")), note=str(a.get("note") or "")[:500])
        return ToolResult(prepared_text(ctx, p, f"Preview: {t.get('displayName') or t['taskType']}"),
                          prepared_structured(ctx, p))

    def task_tool(a):
        ctx = context(a.get("profile"))
        task = _check_task(ctx, a.get("task_id"))
        entry = journal.find_preview(ctx.profile.report_dir, task.get("taskId"), console=ctx.profile.base_url,
                                     profile=ctx.profile.name, task_type=str(task.get("taskType") or ""))
        text = (f"### Task {task.get('taskId')}: {md_escape(task.get('displayName') or task.get('taskType'))}\n\n"
                f"{task.get('mode')} · {task.get('status')} · {counts_line(task)}\n\n"
                f"{task_link(ctx.profile, task.get('taskId'))}")
        if task.get("errorCode") or task.get("errorMessage"):
            text += f"\n\nError: {md_escape(task.get('errorCode') or '')} {md_escape(task.get('errorMessage') or '')}"
        if entry:
            text += f"\n\nPrepared by this server, class {entry.get('class')}."
        return ToolResult(text, {"task": task, "journal": entry})

    def items_tool(a):
        ctx = context(a.get("profile"))
        data = ctx.api.items_page(int(a.get("task_id")), a.get("status"), a.get("error_code"),
                                  int(a.get("offset") or 0), int(a.get("limit") or 50))
        items = list(data.get("items") or [])
        text = f"### Items of task {a.get('task_id')}\n\n" + (_items_table(items) if items else "No items.")
        if data.get("hasMore"):
            text += f"\n\nMore: offset={int(a.get('offset') or 0) + len(items)}."
        return ToolResult(text, {"items": items, "has_more": bool(data.get("hasMore"))})

    def history_tool(a):
        ctx = context(a.get("profile"))
        task_id = int(a.get("task_id"))
        trans, reasons = ctx.api.transitions(task_id), ctx.api.reasons(task_id)
        rows = [[t.get("field"), t.get("beforeValue") or "—", t.get("afterValue") or "—",
                 t.get("itemCount"), t.get("doneCount"), t.get("pendingCount")] for t in trans]
        text = f"### History of task {task_id}"
        if rows:
            text += "\n\n" + md_table(["Field", "Was", "Becomes", "Objects", "Done", "Pending"], rows)
        reasons_text = _reasons_table(reasons)
        if reasons_text:
            text += "\n\n**Refusals**\n\n" + reasons_text
        return ToolResult(text, {"transitions": trans, "reasons": reasons})

    def list_tool(a):
        ctx = context(a.get("profile"))
        scope = a.get("scope") or "MINE"
        page = ctx.api.list_page(scope, a.get("preset"), a.get("query"), int(a.get("offset") or 0),
                                 int(a.get("limit") or 20))
        counts = ctx.api.list_counts(scope)
        tasks = list(page.get("tasks") or [])
        rows = [[t.get("taskId"), t.get("displayName") or t.get("taskType"), t.get("mode"), t.get("status"),
                 (t.get("counts") or {}).get("items"), t.get("author") or ""] for t in tasks]
        text = (f"### Batch tasks ({scope.lower()})\n\nAll {counts.get('all')}, active {counts.get('active')}, "
                f"waiting for a run {counts.get('previewed')}, problems {counts.get('problem')}, "
                f"done {counts.get('done')}, partial {counts.get('partial')}, failed {counts.get('failed')}, "
                f"cancelled {counts.get('cancelled')}")
        if rows:
            text += "\n\n" + md_table(["Task", "Type", "Mode", "Status", "Objects", "Author"], rows)
        return ToolResult(text, {"tasks": tasks, "counts": counts, "has_more": bool(page.get("hasMore"))})

    def cancel_tool(a):
        ctx = context(a.get("profile"))
        task = ctx.api.cancel(int(a.get("task_id")))
        journal.record(ctx.profile.report_dir, "cancel", profile=ctx.profile.name, console=ctx.profile.base_url,
                       task=task,
                       provenance=provenance(ctx))
        return ToolResult(f"### Cancel requested\n\nTask {task.get('taskId')}: {task.get('status')}, "
                          f"{counts_line(task)}. What already ran stays as it is.", {"task": task})

    def run_tool(a):
        ctx = context(a.get("profile"))
        task = _check_task(ctx, a.get("task_id"))
        task_id = task.get("taskId")
        link = task_link(ctx.profile, task_id)
        if task.get("mode") != "PREVIEW" or task.get("status") != "PREVIEWED":
            raise InputError(f"task {task_id} is {task.get('mode')} {task.get('status')}: only a finished preview "
                             f"(PREVIEW, PREVIEWED) can be run")
        task_type = str(task.get("taskType") or "")
        klass = guard.admit(ctx.profile, task)
        if not classes.runs_here(klass):
            return ToolResult(f"### Not run — class {klass} ({classes.NAMES[klass]})\n\nNothing changed. Tasks of "
                              f"this class are run by a person in the console: {link}",
                              {"changed": False, "task": task, "class": klass, "console_link": link})
        done = int((task.get("counts") or {}).get("done") or 0)
        if done == 0:
            raise InputError(f"task {task_id}: the preview has no object marked done; nothing to run")
        warnings: List[str] = []
        answer = ctx.ask(f"Run batch task {task_id} — {task.get('displayName') or task_type} — on {done} objects "
                         f"in profile {ctx.profile.name}? It changes data in the console.",
                         {"run": {"type": "boolean", "title": "Run it"}}, ["run"])
        if answer is None:
            warnings.append("The client cannot ask for confirmation; the task was run without it.")
        elif not answer.get("run"):
            return ToolResult(f"### Not run — declined\n\nNothing changed. Task {task_id} stays prepared: {link}",
                              {"changed": False, "task": task, "declined": True})
        run_task, duplicate, key = ctx.api.run_preview(task_id)
        run_id = run_task.get("taskId")
        if run_id is not None and run_task.get("status") not in api.SETTLED:
            run_task = ctx.api.wait(run_id)
        if duplicate:
            warnings.append("This preview was already run: this is that run, not a new one.")
        journal.record(ctx.profile.report_dir, "run", profile=ctx.profile.name, console=ctx.profile.base_url,
                       task=run_task, klass=klass,
                       idempotency_key=key, provenance=provenance(ctx))
        status = run_task.get("status")
        verb = "CHANGED" if status in ("DONE", "PARTIAL") else "RUNNING" if status not in api.SETTLED else status
        text = [f"### {verb} — task {run_id} from preview {task_id}", counts_line(run_task),
                task_link(ctx.profile, run_id)]
        text += [f"⚠ {md_escape(w)}" for w in warnings]
        if status not in api.SETTLED:
            text.append(f"Still running: read it with batch_task task_id={run_id}.")
        text.append("Way back: plan_revert task_id=" + str(run_id))
        text.append(how_obtained(ctx))
        return ToolResult("\n\n".join(text), {"changed": status in ("DONE", "PARTIAL") or None, "task": run_task,
                                              "class": klass, "warnings": warnings, "idempotency_key": key})

    task_id = {"type": "integer"}
    base = {"profile": PROFILE}
    return [
        Tool("batch_check", "Check the connection: profile, console address, token hint, TLS, where links and the "
             "journal go, warning thresholds, secret sources, and how many batch types this token sees.",
             schema(base), check, "Connection check"),
        Tool("batch_types", "Batch operation types this token may use: object, sources, the class (1 — this server "
             "runs it; 2, 3 — run by a person in the console), max objects.",
             schema({**base, "object_type": {"type": "string"}}), types_tool, "Batch types"),
        Tool("batch_fields", "How to fill a batch type: payload fields and their format, CSV header, attributes "
             "for `changes` types, which dictionaries hold the values, what each refusal code means, and the way "
             "back. Read it before batch_preview of a type.",
             schema({**base, "task_type": {"type": "string"}, "provider_id": {"type": "integer"}}, ["task_type"]),
             fields_tool, "Batch fields"),
        Tool("batch_dictionaries", "Values batch forms take from the console's dictionaries: reason codes for "
             "chargeback, fraud, reversal and faux reversal; manager tariffs (give manager_id) and reseller tariffs "
             "(give reseller_id).",
             schema({**base, "dictionary": {"type": "string", "enum": list(api.BATCH_DICTIONARIES)},
                     "query": {"type": "string", "maxLength": 100}, "manager_id": {"type": "integer"},
                     "reseller_id": {"type": "integer"}}, ["dictionary"]), dict_tool, "Batch dictionaries"),
        Tool("api_hosts", "API hosts a requestor's tokens may be moved to: the console's hosts, by manager "
             "(manager_id — the requestor's manager, found with entity_search entity_type=MANAGER requestor_ids=[…]).",
             schema({**base, "manager_id": {"type": "integer"}, "query": {"type": "string", "maxLength": 100},
                     "api_only": {"type": "boolean", "description": "API hosts only (default true)"}}),
             hosts_tool, "API hosts"),
        Tool("entity_count", "How many entities a selection filter picks — before a task is prepared with it.",
             schema({**base, "level": {"type": "string", "enum": list(api.LEVELS)}, "query": {"type": "string"},
                     "status": {"type": "string", "enum": ["ENABLED", "DISABLED"]}, **FILTER_PROPS}, ["level"]),
             count_tool, "Count a selection"),
        Tool("batch_preview", "Prepare a batch task: the console checks every object and shows what was and what "
             "will be, and why objects are refused. Nothing changes but the task. Objects: ids (order ids from "
             "order_search, entity ids from entity_search), objects with parameters, a selection filter, or csv "
             "in the type's header. A secret goes as secret_ref (source:name) with secret_field naming the payload "
             "field. Class 1 tasks can then be run with batch_run; classes 2 and 3 are run in the console.",
             schema({**base, "task_type": {"type": "string"}, "ids": ID_LIST, "objects": OBJECTS,
                     "selection": SELECTION, "csv": {"type": "string"},
                     "payload": {"type": "object", "additionalProperties": {"type": "string"}},
                     "secret_field": {"type": "string"}, "secret_ref": {"type": "string"},
                     "note": {"type": "string", "description": "why: the finding this task comes from"}},
                    ["task_type"]), preview_tool, "Prepare a batch", effect="prepare"),
        Tool("batch_task", "A batch task: mode, status, counts, error, console link.",
             schema({**base, "task_id": task_id}, ["task_id"]), task_tool, "Batch task"),
        Tool("batch_items", "Objects of a batch task with their status, refusal code and what was / will be.",
             schema({**base, "task_id": task_id, "status": {"type": "string", "enum": list(api.ITEM_STATUSES)},
                     "error_code": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ["task_id"]),
             items_tool, "Batch items"),
        Tool("batch_history", "What a batch task changes, field by field, and why objects were refused.",
             schema({**base, "task_id": task_id}, ["task_id"]), history_tool, "Batch history"),
        Tool("batch_list", "Batch tasks — yours or all you may see — by state: active, problems, waiting for a run "
             "(PREVIEWED), done, partial, failed, cancelled.",
             schema({**base, "scope": {"type": "string", "enum": ["MINE", "ALL"]},
                     "preset": {"type": "string", "enum": list(api.PRESETS)}, "query": {"type": "string"},
                     "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1,
                                                                            "maximum": 100}}),
             list_tool, "Batch tasks"),
        Tool("batch_cancel", "Stop a batch task. What already ran stays; nothing is undone.",
             schema({**base, "task_id": task_id}, ["task_id"]), cancel_tool, "Cancel a batch", effect="prepare"),
        Tool("batch_run", "Run a prepared task of class 1 (reversible) — only the objects its preview marked done, "
             "once. Asks the person first when the client can. Tasks of classes 2 and 3 are not run: the answer "
             "is the console link.",
             schema({**base, "task_id": task_id}, ["task_id"]), run_tool, "Run a batch", effect="write"),
    ]


def searches(context: ContextFn, today: Callable[[], date] = date.today) -> List[Tool]:
    """The shared read-only searches, under this server's read-write client."""
    client_for = lambda p: context(p).api.client                                  # noqa: E731
    return ([entities.tool(client_for, FILTER_PROPS, api.search_filters, PROFILE),
             routing.tool(client_for, PROFILE), restrictions.tool(client_for, PROFILE)]
            + orders.tools(client_for, PROFILE, today, customer_for=lambda p: context(p).profile.show_customer_data))
