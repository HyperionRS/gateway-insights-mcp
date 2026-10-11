# SPDX-License-Identifier: Apache-2.0
"""Finding an entity's id by name or id: the console's entity lists, shared by both servers.

Every analytic tool takes entity ids. A question names a merchant, so the model
first resolves the name here, and that read shows up in «how obtained» like any
other. Each entity type has its own list endpoint, readable with that type's
list right; a type the token owner may not list is refused by the console.

A list row carries more than the id and the name (logins, e-mail addresses,
descriptions). Only the fields in KEEP leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import InputError
from .http import ConsoleClient
from .markdown import md_escape, md_table
from .mcp import Tool, ToolResult, schema


@dataclass(frozen=True)
class EntityList:
    path: str
    rows_key: str       # the response field holding the rows
    id_key: str
    has_status: bool    # whether the request takes a status filter


LISTS: Dict[str, EntityList] = {
    "MERCHANT": EntityList("/merchant/list", "merchants", "merchantId", True),
    "PROVIDER": EntityList("/provider/list", "providers", "providerId", False),
    "MANAGER": EntityList("/manager/list", "managers", "managerId", False),
    "BANK_TERMINAL": EntityList("/bank-terminal/list", "bankTerminals", "bankTerminalId", True),
    "REQUESTOR": EntityList("/requestors/list", "requestors", "requestorId", True),
    "BLUEPRINT": EntityList("/blueprint/list", "blueprints", "blueprintId", True),
    "CHANNEL": EntityList("/channel/list", "channels", "channelId", True),
    "RESELLER": EntityList("/reseller/list", "resellers", "resellerId", False),
}
PATHS = frozenset(e.path for e in LISTS.values())
STATUSES = ("ENABLED", "DISABLED")
MAX_LIMIT = 50
MAX_QUERY = 100


def _currency(row: Dict[str, Any]) -> Optional[str]:
    if row.get("currencyCode"):
        return str(row["currencyCode"])
    if row.get("currenciesFormatted"):
        return str(row["currenciesFormatted"])
    cur = row.get("currency")
    if isinstance(cur, dict) and cur.get("currencyCode"):
        return str(cur["currencyCode"])
    return None


def search(client: ConsoleClient, entity_type: str, query: str = "", *, status: Optional[str] = None,
           filters: Optional[List[Dict[str, Any]]] = None, limit: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
    """Rows {id, name, status, currency} the console's search finds for `query`, and whether more exist.

    `filters` narrow the list the way the console's linked search does: with a
    merchant given, the provider list holds only that merchant's providers.
    """
    text = (entity_type or "").strip().upper()
    if text not in LISTS:
        raise InputError(f"entity_type: {entity_type!r} is not one of {', '.join(LISTS)}")
    spec = LISTS[text]
    query = (query or "").strip()
    if len(query) > MAX_QUERY:
        raise InputError(f"query: at most {MAX_QUERY} characters")
    limit = max(1, min(int(limit), MAX_LIMIT))
    # One row more than asked: its presence says the list goes on. (Some lists add that row themselves,
    # some do not; asking for it is right for both.)
    body: Dict[str, Any] = {"offset": 0, "size": limit + 1, "searchFilters": filters or []}
    if query:
        body["searchText"] = query
    if status:
        status = status.strip().upper()
        if status not in STATUSES:
            raise InputError(f"status: {status!r} is not one of {', '.join(STATUSES)}")
        if not spec.has_status:
            raise InputError(f"status: the {text.lower()} list has no status filter")
        body["status"] = status
    data = client.post(spec.path, body, what=f"{text.lower().replace('_', ' ')} list")
    raw = data.get(spec.rows_key) or []
    rows = [{"id": r.get(spec.id_key), "name": r.get("name"), "status": r.get("status"), "currency": _currency(r)}
            for r in raw if isinstance(r, dict)]
    return rows[:limit], len(rows) > limit


def markdown(entity_type: str, query: str, rows: List[Dict[str, Any]], more: bool) -> str:
    title = f"### {entity_type.capitalize().replace('_', ' ')} search"
    if query:
        title += f": {md_escape(query)}"
    if not rows:
        return f"{title}\n\nNothing found. Try the beginning of the name, a whole word of it, or the id."
    with_currency = any(r["currency"] for r in rows)
    columns = ["Id", "Name", "Status"] + (["Currency"] if with_currency else [])
    table = md_table(columns, [[r["id"], r["name"], r["status"] or "—"] + ([r["currency"] or "—"]
                                                                           if with_currency else [])
                               for r in rows])
    out = [title, table]
    if query:
        out.append("The console's search also looks at fields not shown here (such as a login), so a row can "
                   "match without the query in its name.")
    if more:
        out.append(f"More than {len(rows)} match; narrow the query or the filters.")
    return "\n\n".join(out)


DESCRIPTION = (
    "Find merchants, providers, managers, bank terminals, requestors, blueprints, channels or resellers with "
    "the console's own search — by id, by the beginning of the name or a whole word of it — and get their ids "
    "for the other tools. Use it before passing an entity named in a question to any other tool. The *_ids filters narrow the list "
    "the way linked search does in the console: with merchant_ids given, only that merchant's providers. "
    "To find who owns an entity, search the owner's type with the entity as a filter: entity_type=MANAGER with "
    "blueprint_ids=[…] (or requestor_ids, bank_terminal_ids, merchant_ids) gives its manager; RESELLER the same. "
    "Needs the list right of the entity type; a filter of a type the token owner may not list is dropped by "
    "the console.")


def tool(client_for: Callable[[Optional[str]], ConsoleClient], filter_props: Dict[str, Any],
         search_filters: Callable[[Dict[str, Any]], List[Dict[str, Any]]], profile_prop: Dict[str, Any]) -> Tool:
    """The `entity_search` tool of a server: its client factory, its entity filter arguments and their mapping."""

    def run(a: Dict[str, Any]) -> ToolResult:
        entity_type = (a.get("entity_type") or "").strip().upper()
        query = a.get("query") or ""
        rows, more = search(client_for(a.get("profile")), entity_type, query, status=a.get("status"),
                            filters=search_filters(a), limit=int(a.get("limit") or 20))
        return ToolResult(markdown(entity_type, query, rows, more),
                          {"entity_type": entity_type, "rows": rows, "more": more})

    props = {"profile": profile_prop,
             "entity_type": {"type": "string", "enum": list(LISTS)},
             "query": {"type": "string", "maxLength": MAX_QUERY,
                       "description": "an id, the beginning of a name or a whole word of it, case-insensitive; "
                                      "empty lists all"},
             "status": {"type": "string", "enum": list(STATUSES),
                        "description": "merchants, bank terminals, requestors, blueprints and channels only"},
             "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "description": "rows (default 20)"},
             **filter_props}
    return Tool("entity_search", DESCRIPTION, schema(props, ["entity_type"]), run, "Find an entity")
