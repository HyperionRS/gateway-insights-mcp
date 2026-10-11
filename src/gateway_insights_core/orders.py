# SPDX-License-Identifier: Apache-2.0
"""Order search: the console's Orders screen, shared by every server that needs orders.

One read, `/order/list`, with the screen's rules:

- **Date type** decides which date the period refers to, and with it which filters exist — each mode
  reads its own index, and a filter the index does not carry is refused by the console:

  | date type             | the date                 | simple search | order status | transaction filters | terminal, channel, provider |
  |-----------------------|--------------------------|---------------|--------------|---------------------|-----------------------------|
  | CHANGED (default)     | last change of the order | yes           | yes          | yes                 | yes                         |
  | CREATED               | creation of the order    | no            | yes          | no                  | no (none chosen yet)        |
  | TRANSACTION_CREATED   | creation of a transaction| no            | no           | yes                 | yes                         |

- **Simple search** (CHANGED only): a search type and a text, each type with its own format, length
  and default period, as on the screen.
- Order status and transaction status are exclusive: one or the other.

The rules are checked here, before the request, so the answer says which mode to use instead of a bare
refusal. Customer personal data (names, cardholder, IP) is left out unless the caller asks for it.

Order dictionaries (types, statuses, card types, currencies, error codes) are the console's
autocompletes. They declare no right, which a read-only token is refused; a server with a read-write
token can use them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ids
from .dates import parse_day
from .errors import InputError
from .http import ConsoleClient
from .markdown import md_escape, md_table
from .mcp import Tool, ToolResult, schema

ORDER_LIST = "/order/list"
DICTIONARIES = {
    "order_types": "/autocomplete/order-types",
    "order_statuses": "/autocomplete/order-statuses",
    "transaction_types": "/autocomplete/transaction-types",
    "transaction_statuses": "/autocomplete/transaction-statuses",
    "card_types": "/autocomplete/card-types",
    "currencies": "/autocomplete/currencies",
    "error_codes": "/autocomplete/error-codes",
}
SEARCH_PATHS = frozenset({ORDER_LIST})
DICTIONARY_PATHS = frozenset(DICTIONARIES.values())

DATE_TYPES = ("CHANGED", "CREATED", "TRANSACTION_CREATED")
# Field -> the date types it exists in. Fields not listed work in every mode.
FIELD_MODES = {
    "search_type": ("CHANGED",),
    "search_text": ("CHANGED",),
    "order_statuses": ("CHANGED", "CREATED"),
    "transaction_types": ("CHANGED", "TRANSACTION_CREATED"),
    "transaction_statuses": ("CHANGED", "TRANSACTION_CREATED"),
    "order_error": ("CHANGED", "TRANSACTION_CREATED"),
}
# Entity filters: argument -> console entity type. A filter of the last three is refused in CREATED:
# an order gets its terminal (and with it channel and provider) only after creation.
ENTITY_FILTERS = ids.ENTITY_FILTERS
NOT_IN_CREATED = ("provider_ids", "bank_terminal_ids", "channel_ids")
LIST_FILTERS = {"order_types": "orderTypes", "order_statuses": "orderStatuses",
                "transaction_types": "transactionTypes", "transaction_statuses": "transactionStatuses",
                "card_types": "cardTypes", "currencies": "currency"}
MAX_ORDER_IDS = 500
MAX_IDS = 100
MAX_PAGE = 100
ALL_TIME = ("1000-01-01", "2999-12-31")
_MAX_UINT = 4294967295
_MAX_AMOUNT = 999999999999.9994
_CARD_6AND4 = re.compile(r"^(\d{6})[\d*X +]{0,9}(\d{4})$")
_ID_SEPARATOR = re.compile(r"[\s,;]+")


@dataclass(frozen=True)
class SearchType:
    key: str
    group: str
    label: str
    period: str = ""          # default period in CHANGED: all, year, month, week; "" — two days
    max_length: int = 0
    fmt: str = ""             # uint, amount, card; "" — free text
    many: bool = False        # a list of numbers, up to MAX_ORDER_IDS


SEARCH_TYPES: Dict[str, SearchType] = {s.key: s for s in [
    SearchType("orderId", "order", "order number", "all", fmt="uint", many=True),
    SearchType("merchantInvoiceNumber", "order", "merchant's order number", "year", 128, many=True),
    SearchType("processorOrderNo", "order", "provider's order number", "year", 128, many=True),
    SearchType("transactionAmount", "order", "amount", "week", fmt="amount"),
    SearchType("sourceRRN", "order", "RRN", "year", 20),
    SearchType("purpose", "order", "purpose", "month", 128),
    SearchType("customerPhone", "customer", "phone", "month", 128),
    SearchType("customerIp", "customer", "browser IP", "month", 64),
    SearchType("customerEMail", "customer", "e-mail", "year", 128),
    SearchType("ipAddressCountry", "customer", "IP country code", "week"),
    SearchType("sourceAuthCode", "card", "approval code", "month", 10),
    SearchType("sourceARN", "card", "ARN", "year", 64),
    SearchType("sourceBIN", "card", "BIN, 6 digits", "week", fmt="uint"),
    SearchType("sourceBIN8", "card", "BIN, 8 digits", "week", fmt="uint"),
    SearchType("sourceLast4", "card", "last 4 digits", "month", 7),
    SearchType("card6and4", "card", "first 6 and last 4", "year", fmt="card"),
    SearchType("sourceFromOrderNo", "card", "card of order number", "year", fmt="uint"),
    SearchType("cardHolder", "card", "cardholder", "year", 128),
    SearchType("sourceCountry", "card", "card country code", "week"),
    SearchType("sourceCardBankName", "card", "issuing bank", "", 128),
    SearchType("destinationBIN", "receiver", "receiver BIN, 6 digits", "week", fmt="uint"),
    SearchType("destinationBIN8", "receiver", "receiver BIN, 8 digits", "week", fmt="uint"),
    SearchType("destinationLast4", "receiver", "receiver last 4 digits", "year", 7),
    SearchType("receiverCard6and4", "receiver", "receiver first 6 and last 4", "year", fmt="card"),
    SearchType("destinationFromOrderNo", "receiver", "receiver card of order number", "year", fmt="uint"),
    SearchType("destinationCardBankName", "receiver", "receiver issuing bank", "", 128),
]}
# Search types that send a customer's personal data to whoever runs the model.
PERSONAL_SEARCH = frozenset({"customerPhone", "customerIp", "customerEMail", "cardHolder"})


def _ids(values: Any, what: str, limit: int = MAX_IDS) -> List[int]:
    return ids.int_list(values, what, limit)


def _when(text: Any, what: str, end: bool) -> str:
    """'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]' (UTC) -> the console's form."""
    value = str(text).strip().replace("T", " ")
    if len(value) == 10:
        parse_day(value, what)
        return value
    m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$", value)
    if not m:
        raise InputError(f"{what}: expected YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS] (UTC), got {text!r}")
    parse_day(m.group(1), what)
    hh, mm, ss = int(m.group(2)), int(m.group(3)), int(m.group(4) or (59 if end else 0))
    if hh > 23 or mm > 59 or ss > 59:
        raise InputError(f"{what}: {text!r} is not a time of day")
    return f"{m.group(1)} {hh:02d}:{mm:02d}:{ss:02d}"


def default_period(date_type: str, search: Optional[SearchType], has_entities: bool,
                   today: date) -> Optional[Tuple[str, str]]:
    """The screen's default window; None — no period (order number, all time)."""
    def back(days: int) -> Tuple[str, str]:
        return (today - timedelta(days=days)).isoformat(), today.isoformat()

    if date_type != "CHANGED":
        return back(2)
    period = search.period if search else ""
    if period == "all":
        return None
    if period == "year":
        return back(365)
    if period == "month":
        return back(30)
    if has_entities:      # chosen entities narrow the result on their own: a week or two days widen to a month
        return back(30)
    if period == "week":
        return back(7)
    return back(2)


def _search_text(search: SearchType, text: Any) -> Tuple[str, Optional[str]]:
    """Validated text and, for a full card number folded to 6+4, a note."""
    value = str(text or "").strip()
    if not value:
        raise InputError(f"search_text is required with search_type {search.key}")
    note = None
    if search.many:
        parts = [p for p in _ID_SEPARATOR.split(value) if p]
        if len(parts) > MAX_ORDER_IDS:
            raise InputError(f"search_text: at most {MAX_ORDER_IDS} numbers, got {len(parts)}")
        if search.fmt == "uint" and any(not p.isdigit() or int(p) > _MAX_UINT for p in parts):
            raise InputError(f"search_text: {search.label} takes whole numbers up to {_MAX_UINT}")
        if search.max_length and any(len(p) > search.max_length for p in parts):
            raise InputError(f"search_text: each {search.label} is at most {search.max_length} characters")
        return " ".join(parts), None
    if search.fmt == "uint" and (not re.fullmatch(r"\d{1,10}", value) or int(value) > _MAX_UINT):
        raise InputError(f"search_text: {search.label} takes a whole number up to {_MAX_UINT}")
    if search.fmt == "amount" and (not re.fullmatch(r"\d+(\.\d+)?", value) or float(value) > _MAX_AMOUNT):
        raise InputError(f"search_text: {search.label} takes a number like 10 or 10.50")
    if search.fmt == "card":
        if re.fullmatch(r"[\d -]{13,23}", value):     # a full number with separators: digits only
            value = re.sub(r"[ -]", "", value)
        m = _CARD_6AND4.match(value)
        if not m:
            raise InputError(f"search_text: {search.label} takes 6 digits and 4 digits, e.g. 411111*1111")
        folded = f"{m.group(1)}*{m.group(2)}"
        if re.sub(r"\D", "", value) != m.group(1) + m.group(2):
            note = ("A full card number was given: it was folded to its first 6 and last 4 digits for the "
                    "search, but the full number has already been sent to the model provider.")
        value = folded
    if search.max_length and len(value) > search.max_length:
        raise InputError(f"search_text: {search.label} is at most {search.max_length} characters")
    return value, note


def build_query(args: Dict[str, Any], today: date) -> Tuple[Dict[str, Any], List[str]]:
    """The /order/list body from tool arguments, and the warnings that go with it."""
    date_type = str(args.get("date_type") or "CHANGED").strip().upper()
    if date_type not in DATE_TYPES:
        raise InputError(f"date_type: {date_type!r} is not one of {', '.join(DATE_TYPES)}")
    warnings: List[str] = []

    for field, modes in FIELD_MODES.items():
        if args.get(field) not in (None, "", []) and date_type not in modes:
            raise InputError(f"{field} does not exist with date_type {date_type}; it works with "
                             f"{' and '.join(modes)}")
    if date_type == "CREATED":
        used = [f for f in NOT_IN_CREATED if args.get(f)]
        if used:
            raise InputError(f"{', '.join(used)}: an order has no terminal, channel or provider at creation; "
                             "use date_type CHANGED or TRANSACTION_CREATED")
    if args.get("order_statuses") and args.get("transaction_statuses"):
        raise InputError("order_statuses and transaction_statuses are exclusive: use one of them")

    body: Dict[str, Any] = {"dateType": date_type}
    search = None
    if args.get("search_type"):
        search = SEARCH_TYPES.get(str(args["search_type"]))
        if search is None:
            raise InputError(f"search_type: {args['search_type']!r} is not known; one of "
                             f"{', '.join(SEARCH_TYPES)}")
        text, note = _search_text(search, args.get("search_text"))
        body["searchType"], body["searchText"] = search.key, text
        if note:
            warnings.append(note)
        if search.key in PERSONAL_SEARCH:
            warnings.append(f"This search sends a customer's {search.label} to the model provider with the "
                            "question and the result.")
    elif args.get("search_text"):
        raise InputError("search_text needs a search_type")

    filters = []
    for param, entity in ENTITY_FILTERS.items():
        ids = _ids(args.get(param), param)
        if ids:
            filters.append({"entityType": entity, "items": [{"id": i, "name": ""} for i in ids]})
    if filters:
        body["searchFilters"] = filters
    for param, key in LIST_FILTERS.items():
        ids = _ids(args.get(param), param)
        if ids:
            body[key] = [{"id": i, "name": ""} for i in ids]
    if args.get("order_error") not in (None, ""):
        body["orderError"] = {"id": _ids(args["order_error"], "order_error", 1)[0], "name": ""}

    has_from, has_to = args.get("date_from") not in (None, ""), args.get("date_to") not in (None, "")
    if has_from or has_to:
        start = _when(args.get("date_from") or args.get("date_to"), "date_from", end=False)
        end = _when(args.get("date_to") or args.get("date_from"), "date_to", end=True)
        if start > end:
            raise InputError("date_from is after date_to")
        body["period"] = {"startDate": start, "endDate": end}
    elif args.get("all_time"):
        if date_type != "CHANGED":
            raise InputError(f"all_time works with date_type CHANGED only; {date_type} needs a period")
        body["period"] = {"startDate": ALL_TIME[0], "endDate": ALL_TIME[1]}
    else:
        window = default_period(date_type, search, bool(filters), today)
        if window:
            body["period"] = {"startDate": window[0], "endDate": window[1]}

    size = int(args.get("limit") or 50)
    if not 1 <= size <= MAX_PAGE:
        raise InputError(f"limit: 1..{MAX_PAGE}")
    offset = int(args.get("offset") or 0)
    if offset < 0:
        raise InputError("offset is not negative")
    body.update({"offset": offset, "size": size})
    return body, warnings


def _info(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict) and (value.get("id") is not None or value.get("name")):
        return {"id": value.get("id"), "name": value.get("name")}
    return None


def order_row(o: Dict[str, Any], customer: bool = False) -> Dict[str, Any]:
    """The fields that leave the tool. Customer data only when asked for."""
    status = o.get("orderStatus") or {}
    card = o.get("card") or {}
    row = {
        "order_id": o.get("orderId"), "merchant_order_id": o.get("merchantOrderId"),
        "status": status.get("status"), "type": status.get("type"), "status_full": status.get("fullName"),
        "amount": o.get("amount"), "currency": o.get("currencyCode"),
        "created": o.get("createdFormatted"), "changed": o.get("statusChangedFormatted"),
        "search_date": o.get("searchDateFormatted"),
        "merchant": _info(o.get("merchant")), "requestor": _info(o.get("requestor")),
        "blueprint": _info(o.get("project")), "provider": _info(o.get("provider")),
        "bank_terminal": _info(o.get("providerTerminal")), "channel": _info(o.get("channel")),
        "provider_code": o.get("providerCode"),
        "card": card.get("panMasked") or o.get("cardFormatted"), "card_type": card.get("type"),
        "transactions": [{"id": t.get("txId"), "type": t.get("txType"), "status": t.get("txStatus"),
                          "amount": t.get("txAmount")} for t in (o.get("transactions") or [])
                         if isinstance(t, dict)],
    }
    if customer:
        country = o.get("browserCountry") or {}
        row["customer"] = {"name": o.get("customerName"), "cardholder": card.get("cardholder") or o.get("cardholder"),
                           "ip": o.get("browserIp"), "ip_country": country.get("code")}
    return row


def search(client: ConsoleClient, args: Dict[str, Any], today: date, *,
           customer: bool = False) -> Tuple[List[Dict[str, Any]], bool, Dict[str, Any], List[str]]:
    """(rows, has_more, the request body, warnings)."""
    body, warnings = build_query(args, today)
    data = client.post(ORDER_LIST, body, what="order search")
    rows = [order_row(o, customer) for o in (data.get("orders") or []) if isinstance(o, dict)]
    if customer:
        warnings.append("Customer personal data is shown (profile setting show_customer_data).")
    return rows, bool(data.get("hasMore")), body, warnings


def _name(info: Optional[Dict[str, Any]]) -> str:
    return f"{info.get('name') or '—'} ({info.get('id')})" if info else "—"


def markdown(rows: List[Dict[str, Any]], has_more: bool, body: Dict[str, Any], warnings: List[str]) -> str:
    period = body.get("period")
    span = f"{period['startDate']} … {period['endDate']}" if period else "all time"
    head = f"### Orders — {body['dateType'].lower().replace('_', ' ')} {span}"
    if body.get("searchType"):
        head += f", {SEARCH_TYPES[body['searchType']].label}"
    out = [head] + [f"⚠ {md_escape(w)}" for w in warnings]
    if not rows:
        out.append("Nothing found.")
        return "\n\n".join(out)
    customer = any("customer" in r for r in rows)
    columns = ["Order", "Status", "Amount", "Changed", "Merchant", "Provider / terminal", "Card"]
    if customer:
        columns.append("Customer")
    table = []
    for r in rows:
        line = [r["order_id"], f"{r['type'] or ''} {r['status'] or ''}".strip(), f"{r['amount'] or ''} {r['currency'] or ''}",
                r["changed"], _name(r["merchant"]),
                f"{(r['provider'] or {}).get('name') or '—'} / {(r['bank_terminal'] or {}).get('name') or '—'}",
                r["card"] or "—"]
        if customer:
            c = r.get("customer") or {}
            line.append(", ".join(str(v) for v in (c.get("name"), c.get("ip")) if v) or "—")
        table.append(line)
    out.append(md_table(columns, table))
    statuses: Dict[str, int] = {}
    for r in rows:
        statuses[r["status"] or "—"] = statuses.get(r["status"] or "—", 0) + 1
    out.append("On this page: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items(), key=lambda kv: -kv[1])))
    if has_more:
        out.append(f"More orders match: next page with offset={body['offset'] + body['size']}.")
    return "\n\n".join(out)


SEARCH_TYPE_HELP = "; ".join(f"{s.key} — {s.label}" for s in SEARCH_TYPES.values())
SEARCH_DESCRIPTION = (
    "Find orders as on the console's Orders screen. date_type picks the date the period refers to and with it "
    "the filters that exist: CHANGED (default; last change — everything, and the only mode with simple search), "
    "CREATED (order creation — no transaction filters, no provider, terminal or channel), TRANSACTION_CREATED "
    "(transaction creation — no order status). order_statuses and transaction_statuses are exclusive. Ids for "
    "type, status, card type, currency and error filters come from order_dictionaries; entity ids from "
    "entity_search. Customer personal data is not shown unless the profile allows it.")


def tools(client_for: Callable[[Optional[str]], ConsoleClient], profile_prop: Dict[str, Any],
          today: Callable[[], date] = date.today, customer_for: Callable[[Optional[str]], bool] = lambda p: False,
          dictionaries: bool = True) -> List[Tool]:
    """`order_search` and, when the server's token may read them, `order_dictionaries`."""

    def run_search(a: Dict[str, Any]) -> ToolResult:
        rows, more, body, warnings = search(client_for(a.get("profile")), a, today(),
                                            customer=customer_for(a.get("profile")))
        return ToolResult(markdown(rows, more, body, warnings),
                          {"rows": rows, "has_more": more, "order_ids": [r["order_id"] for r in rows],
                           "request": body, "warnings": warnings})

    ids = {"type": "array", "items": {"type": "integer"}}
    props = {
        "profile": profile_prop,
        "date_type": {"type": "string", "enum": list(DATE_TYPES)},
        "date_from": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM-DD HH:MM (UTC); default: the "
                      "screen's window for the search type"},
        "date_to": {"type": "string", "description": "inclusive; YYYY-MM-DD or YYYY-MM-DD HH:MM (UTC)"},
        "all_time": {"type": "boolean", "description": "no period limit (CHANGED only)"},
        "search_type": {"type": "string", "enum": list(SEARCH_TYPES), "description": SEARCH_TYPE_HELP},
        "search_text": {"type": "string", "description": "the value for search_type; several order numbers "
                        f"separated by spaces or commas, up to {MAX_ORDER_IDS}"},
        **{k: dict(ids, description=f"{k.replace('_', ' ')} from order_dictionaries") for k in LIST_FILTERS},
        "order_error": {"type": "integer", "description": "error code id from order_dictionaries"},
        **{k: dict(ids, description=f"only these {v.lower().replace('_', ' ')} ids") for k, v in ENTITY_FILTERS.items()},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE, "description": "rows (default 50)"},
        "offset": {"type": "integer", "minimum": 0},
    }
    out = [Tool("order_search", SEARCH_DESCRIPTION, schema(props), run_search, "Find orders")]

    if dictionaries:
        def run_dict(a: Dict[str, Any]) -> ToolResult:
            name = str(a.get("dictionary") or "")
            if name not in DICTIONARIES:
                raise InputError(f"dictionary: one of {', '.join(DICTIONARIES)}")
            query = str(a.get("query") or "").strip()[:100]
            data = client_for(a.get("profile")).post(DICTIONARIES[name], {"offset": 0, "size": 100,
                                                                          "searchText": query},
                                                     what=f"{name.replace('_', ' ')} dictionary")
            items = [{"id": i.get("id"), "name": i.get("name")} for i in (data.get("items") or [])
                     if isinstance(i, dict)]
            text = f"### {name.replace('_', ' ').capitalize()}" + (f": {md_escape(query)}" if query else "")
            text += "\n\n" + (md_table(["Id", "Name"], [[i["id"], i["name"]] for i in items]) if items
                              else "Nothing found.")
            if data.get("hasMore"):
                text += "\n\nMore entries match: narrow the query."
            return ToolResult(text, {"dictionary": name, "items": items, "has_more": bool(data.get("hasMore"))})

        out.append(Tool("order_dictionaries", "Ids for order_search filters: order types and statuses, transaction "
                        "types and statuses, card types, currencies, error codes — by a part of the name.",
                        schema({"profile": profile_prop, "dictionary": {"type": "string", "enum": list(DICTIONARIES)},
                                "query": {"type": "string", "maxLength": 100}}, ["dictionary"]),
                        run_dict, "Order dictionaries"))
    return out
