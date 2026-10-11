# SPDX-License-Identifier: Apache-2.0
"""Raw tools: one per endpoint, a normalised table plus the JSON.

Fields the console hides from the token owner arrive as null and are shown as
«no access» — never as zero, and never rebuilt from other fields.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from gateway_insights_core.dates import window
from gateway_insights_core.markdown import NO_ACCESS, md_escape, md_table
from gateway_insights_core.mcp import Tool, ToolResult, schema
from gateway_insights_core.numbers import (fmt_count, fmt_money, fmt_number, fmt_pp, fmt_rate, fmt_signed,
                                           ratio, to_decimal)

from . import api
from .api import ExecApi, Scope

Context = Callable[[Optional[str]], "ToolContext"]


class ToolContext:
    """What a tool call needs: the API for the chosen profile, today, the default currency.

    A context is built per tool call, so its client's call log holds exactly the reads of that call —
    what a business result prints as «how obtained».
    """

    def __init__(self, exec_api: ExecApi, today: date, currency_id: Optional[int]) -> None:
        self.api = exec_api
        self.today = today
        self.currency_id = currency_id

    @property
    def profile(self):
        return self.api.client.profile


# ---- argument schemas -----------------------------------------------------------------------------

PROFILE = {"type": "string", "description": "configuration profile; default: the configured default"}
WINDOW = {
    "date_from": {"type": "string", "description": "first day, YYYY-MM-DD (default: 7 days to date_to)"},
    "date_to": {"type": "string", "description": "last day inclusive, YYYY-MM-DD (default: today)"},
    "base_from": {"type": "string", "description": "first day of the comparison base, DAY grain only; "
                  "with base_to. Default base: the same number of days right before the period"},
    "base_to": {"type": "string", "description": "last day of the base inclusive; must not overlap the period"},
    "grain": {"type": "string", "enum": list(api.GRAINS),
              "description": "DAY (the window above) or HOUR (always the last 24 hours vs the 24 before)"},
    "currency_id": {"type": "integer", "description": "display currency id; default: the profile's, "
                    "else the token owner's profile currency"},
}
FILTER_PROPS = {k: {"type": "array", "items": {"type": "integer"},
                    "description": f"narrow to these {v.lower().replace('_', ' ')} ids (≤ {api.MAX_IDS})"}
                for k, v in api.FILTERS.items()}
FLOW = {"type": "string", "enum": list(api.FLOWS), "description": "deposits, withdrawals or both"}
# Business tools work by day: the window, the base, the currency and the entity filters.
FILTER_PROFILE_PROPS = {"profile": PROFILE, **{k: WINDOW[k] for k in ("date_from", "date_to", "base_from",
                                                                        "base_to", "currency_id")},
                        **FILTER_PROPS}
ENTITY = {"type": "string", "enum": list(api.ENTITIES), "description": "entity to break down by"}


def _props(*groups: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"profile": PROFILE}
    for g in groups:
        out.update(g)
    out.update(extra)
    return out


# ---- value helpers ------------------------------------------------------------------------------

def d(row: Dict[str, Any], key: str) -> Optional[Decimal]:
    return to_decimal(row.get(key))


def hidden_or(value: Optional[Decimal], render: Callable[[Optional[Decimal]], str]) -> str:
    return NO_ACCESS if value is None else render(value)


def direction(value: Any) -> str:
    n = to_decimal(value)
    if n is None:
        return ""
    return "▲ good" if n > 0 else "▼ bad" if n < 0 else "flat"


def period_line(s: Scope) -> str:
    base = s.effective_base
    text = f"Period: {s.current}" if s.grain == "DAY" else "Period: last 24 hours"
    text += f" · base: {base}" if base else (" · base: the 24 hours before" if s.grain == "HOUR" else "")
    return text


def unit_of_top(metric: str) -> str:
    return {"DECLINE": "rate", "CONVERSION": "rate", "AMOUNT": "money", "AVG_TICKET": "money",
            "MANAGER_MARGIN": "money", "RESELLER_MARGIN": "money", "TOTAL_MARGIN": "money"}.get(metric, "count")


def render_unit(value: Optional[Decimal], unit: str, currency: str = "") -> str:
    if unit == "rate":
        return fmt_rate(value)
    if unit == "money":
        return fmt_money(value, currency)
    if unit == "count":
        return fmt_count(value)
    return fmt_number(value)


# ---- exec_kpi -----------------------------------------------------------------------------------

KPI_METRICS = [
    # label, field suffix, unit, base suffix, delta suffix, direction suffix
    ("Transactions", "Total", "count", "TotalPrev", None, None),
    ("Approved", "Approved", "count", "ApprovedPrev", "ApprovedDelta", "ApprovedDir"),
    ("Approved amount", "ApprovedAmount", "money", "ApprovedAmountPrev", "ApprovedAmountDelta", "ApprovedAmountDir"),
    ("Declined", "Declined", "count", "DeclinedPrev", "DeclinedDelta", "DeclinedDir"),
    ("Filtered", "Filtered", "count", "FilteredPrev", "FilteredDelta", "FilteredDir"),
    ("Approval rate", "ApprovalRate", "rate", "ApprovalRatePrev", "ApprovalRateDeltaPp", "ApprovalRateDir"),
    ("Decline rate", "DeclineRate", "rate", "DeclineRatePrev", "DeclineRateDeltaPp", "DeclineRateDir"),
    ("Average ticket", "AvgTicket", "money", "AvgTicketPrev", None, "AvgTicketDir"),
]
KPI_MIX = [("Issuer declines (share of declines)", "MixIssuer"),
           ("Processor declines (share of declines)", "MixProcessor"),
           ("Retryable declines (share of all)", "MixRetryable"),
           ("3-D Secure related (share of all)", "Mix3ds")]

KPI_CAVEATS = [
    "Average ticket = approved amount / all transactions, declines and filtered included.",
    "Approval rate counts filtered transactions in the denominator.",
    "Amounts are in the display currency at the latest rate, for both periods; not for reconciliation.",
]


def kpi_structured(data: Dict[str, Any]) -> Dict[str, Any]:
    flows: Dict[str, Any] = {}
    for flow in ("deposit", "withdrawal"):
        metrics = {}
        for label, key, unit, base, delta, dirn in KPI_METRICS:
            metrics[key] = {
                "label": label, "unit": unit, "now": d(data, flow + key), "base": d(data, flow + base),
                "delta": d(data, flow + delta) if delta else None,
                "direction": int(d(data, flow + dirn)) if dirn and d(data, flow + dirn) is not None else None,
            }
        metrics["mix"] = {key: d(data, flow + key) for _, key in KPI_MIX}
        flows[flow] = metrics
    return {"currency": data.get("displayCurrency") or "",
            "total": {"now": d(data, "totalCount"), "base": d(data, "totalCountPrev"),
                      "delta": d(data, "totalCountDelta")},
            "flows": flows}


def kpi_markdown(s: Scope, data: Dict[str, Any]) -> str:
    st = kpi_structured(data)
    cur = st["currency"]
    out = ["### Executive KPI", period_line(s) + (f" · {md_escape(cur)}" if cur else "")]
    for flow in ("deposit", "withdrawal"):
        rows = []
        for label, key, unit, *_ in KPI_METRICS:
            m = st["flows"][flow][key]
            delta = fmt_pp(m["delta"]) if unit == "rate" else (
                fmt_signed(m["delta"], 0 if unit == "count" else 2) if m["delta"] is not None else "—")
            rows.append([label, render_unit(m["now"], unit, cur), render_unit(m["base"], unit, cur), delta,
                         direction(m["direction"])])
        out.append(f"**{flow.capitalize()}s**\n\n" + md_table(["Metric", "Now", "Base", "Change", ""], rows))
        mix = st["flows"][flow]["mix"]
        out.append(md_table(["Decline breakdown", "Now"],
                            [[label, fmt_rate(mix[key])] for label, key in KPI_MIX]))
    out.append("Caveats: " + " ".join(KPI_CAVEATS))
    return "\n\n".join(out)


# ---- exec_trend ---------------------------------------------------------------------------------

TREND_UNIT = {"TOTAL_COUNT": "count", "APPROVAL": "count", "FILTERED": "count", "AMOUNT": "money",
              "AVG_TICKET": "money", "CONVERSION": "rate", "DECLINE": "rate"}
MAX_MD_ROWS = 200


def trend_value(num: Any, den: Any) -> Optional[Decimal]:
    """A bucket's value: a count or amount as is; a rate as numerator / denominator."""
    n, dn = to_decimal(num), to_decimal(den)
    if dn is None:
        return n
    return ratio(n, dn)


def trend_structured(rows: List[Dict[str, Any]], metric: str) -> Dict[str, Any]:
    points = []
    for r in rows:
        points.append({
            "series": r.get("seriesName") or r.get("seriesKind"), "series_kind": r.get("seriesKind"),
            "series_id": r.get("seriesId"), "period": r.get("periodFlag"), "bucket": r.get("bucketKey"),
            "deposit": trend_value(r.get("trendNumDeposit"), r.get("trendDenDeposit")),
            "withdrawal": trend_value(r.get("trendNumWithdrawal"), r.get("trendDenWithdrawal")),
        })
    return {"metric": metric, "unit": TREND_UNIT[metric], "points": points}


def trend_markdown(s: Scope, rows: List[Dict[str, Any]], metric: str, currency: str) -> str:
    st = trend_structured(rows, metric)
    unit = st["unit"]
    table_rows = [[p["series"], p["period"], p["bucket"], render_unit(p["deposit"], unit, currency),
                   render_unit(p["withdrawal"], unit, currency)] for p in st["points"][:MAX_MD_ROWS]]
    out = [f"### Trend: {metric.lower().replace('_', ' ')}", period_line(s),
           md_table(["Series", "Period", "Bucket", "Deposits", "Withdrawals"], table_rows)]
    if len(st["points"]) > MAX_MD_ROWS:
        out.append(f"…{len(st['points']) - MAX_MD_ROWS} more rows in the structured result.")
    if unit == "rate":
        out.append("A rate over several buckets is Σnumerator / Σdenominator, not an average of the rates.")
    return "\n\n".join(out)


# ---- exec_top -----------------------------------------------------------------------------------

TIER_FIELDS = [("Blueprint rate", "flowManagerRate"), ("Bank cost", "flowBankCost"),
               ("Manager margin", "flowManagerMargin"), ("Reseller margin", "flowResellerMargin")]


def top_structured(rows: List[Dict[str, Any]], metric: str) -> Dict[str, Any]:
    out = []
    for r in rows:
        item = {
            "grand_total": bool(to_decimal(r.get("isGrandTotal"))), "id": r.get("entityId"),
            "name": r.get("entityName"), "type": r.get("entityType"),
            "metric": d(r, "metricValue"), "metric_base": d(r, "metricValuePrev"),
            "direction": int(d(r, "metricDirection")) if d(r, "metricDirection") is not None else None,
            "approved": d(r, "flowApproved"), "declined": d(r, "flowDeclined"), "total": d(r, "flowTotal"),
            "approved_amount": d(r, "flowApprovedAmount"), "approved_amount_base": d(r, "flowApprovedAmountPrev"),
            "approval_rate": d(r, "flowApprovalRate"), "approval_rate_base": d(r, "flowApprovalRatePrev"),
            "avg_ticket": d(r, "flowAvgTicket"),
        }
        # Tariff tiers: null means the token owner may not see them — kept as null, never derived.
        for _, key in TIER_FIELDS:
            item[key] = d(r, key)
            item[key + "Prev"] = d(r, key + "Prev")
        out.append(item)
    return {"metric": metric, "unit": unit_of_top(metric), "rows": out}


def top_markdown(s: Scope, rows: List[Dict[str, Any]], metric: str, entity: str, currency: str) -> str:
    st = top_structured(rows, metric)
    unit = st["unit"]
    table_rows = []
    for r in st["rows"]:
        name = "Total" if r["grand_total"] else (f"{r['id']} · {r['name']}" if r["id"] is not None else r["name"])
        table_rows.append([name, render_unit(r["metric"], unit, currency), render_unit(r["metric_base"], unit, currency),
                           direction(r["direction"]), fmt_money(r["approved_amount"], currency),
                           fmt_rate(r["approval_rate"])]
                          + [hidden_or(r[key], lambda v: fmt_money(v, currency)) for _, key in TIER_FIELDS])
    out = [f"### Top {entity.lower()}s by {metric.lower().replace('_', ' ')}", period_line(s),
           md_table(["Entity", "Metric", "Base", "", "Approved amount", "Approval rate"]
                    + [label for label, _ in TIER_FIELDS], table_rows),
           f"«{NO_ACCESS}»: the token owner lacks the tariff right for that tier; the value is not shown "
           "or computed. Earnings are summed over all statuses."]
    return "\n\n".join(out)


# ---- holds --------------------------------------------------------------------------------------

def hold_top_markdown(rows: List[Dict[str, Any]], entity: str) -> str:
    cur = next((r.get("displayCurrency") for r in rows if r.get("displayCurrency")), "")
    table_rows = [["Total" if to_decimal(r.get("isGrandTotal")) else f"{r.get('entityId')} · {r.get('entityName')}",
                   fmt_money(d(r, "merchantHold"), cur), fmt_money(d(r, "bankHold"), cur)] for r in rows]
    return "\n\n".join([
        f"### Money on hold now, by {entity.lower()}",
        md_table(["Entity", "Held from merchants", "Held by banks"], table_rows),
        "A snapshot as of the console's last nightly refresh. Manager and provider are the current owners "
        "of the project and the gate, not as of the transaction."])


def hold_schedule_markdown(rows: List[Dict[str, Any]], days: int) -> str:
    cur = next((r.get("displayCurrency") for r in rows if r.get("displayCurrency")), "")
    table_rows = []
    for r in rows:
        day = r.get("releaseDay")
        label = f"later than {days} days" if day in (None, "") else f"{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:8]}"
        table_rows.append([label, fmt_money(d(r, "merchantHold"), cur), fmt_money(d(r, "bankHold"), cur)])
    return "\n\n".join([f"### Hold release schedule, next {days} days",
                        md_table(["Release day", "From merchants", "By banks"], table_rows)])


# ---- transaction monitor ------------------------------------------------------------------------

def monitor_total_structured(tiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"type": t.get("type"), "title": t.get("title"),
             "approved": to_decimal(t.get("approvedCountFormatted")),
             "declined": to_decimal(t.get("declinedCountFormatted")),
             "filtered": to_decimal(t.get("filteredCountFormatted")),
             "amount": to_decimal(t.get("amountFormatted")), "currency": t.get("amountCurrencyCode") or "",
             "approval_ratio": to_decimal(t.get("approvalRatioFormatted")),
             "counts_only": bool(t.get("countsOnly"))} for t in tiles]


def monitor_total_markdown(period, tiles: List[Dict[str, Any]]) -> str:
    st = monitor_total_structured(tiles)
    rows = [[t["type"], fmt_count(t["approved"]), fmt_count(t["declined"]), fmt_count(t["filtered"]),
             fmt_money(t["amount"], t["currency"]), fmt_rate(t["approval_ratio"])] for t in st]
    return "\n\n".join([f"### Transaction monitor, {period}",
                        md_table(["Operation", "Approved", "Declined", "Filtered", "Amount", "Approval"], rows)])


def _id_and_name(item: Dict[str, Any]):
    ident = next((v for k, v in item.items() if k.endswith("Id")), None)
    name = next((v for k, v in item.items() if k.endswith("Name")), "")
    return ident, name


def monitor_entity_structured(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        ident, name = _id_and_name(it)
        out.append({"id": ident, "name": name, "currency": it.get("currencyCode") or "",
                    "amount": to_decimal(it.get("transactionAmountFormatted")),
                    "approved": to_decimal(it.get("approvedCountFormatted")),
                    "declined": to_decimal(it.get("declinedCountFormatted")),
                    "filtered": to_decimal(it.get("filteredCountFormatted"))})
    return out


def _currency(a: Dict[str, Any], ctx: ToolContext) -> Optional[int]:
    value = a.get("currency_id", ctx.currency_id)
    return None if value in (None, "") else int(value)


# ---- the tools ----------------------------------------------------------------------------------

def build(context: Context) -> List[Tool]:
    def run_kpi(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        s = api.scope(a, ctx.today, ctx.currency_id)
        data = ctx.api.kpi(s)
        return ToolResult(kpi_markdown(s, data), {"period": str(s.current), "kpi": kpi_structured(data)})

    def run_trend(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        s = api.scope(a, ctx.today, ctx.currency_id)
        metric = api.choice(a.get("metric"), api.TREND_METRICS, "metric")
        rows = ctx.api.trend(s, metric, a.get("flow") or "BOTH", a.get("entity_type") or "MERCHANT",
                             a.get("entity_ids") or (), separate=a.get("split") == "SEPARATE")
        return ToolResult(trend_markdown(s, rows, metric, ""),
                          trend_structured(rows, metric))

    def run_top(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        s = api.scope(a, ctx.today, ctx.currency_id)
        metric = api.choice(a.get("metric"), api.TOP_METRICS, "metric")
        entity = api.choice(a.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
        top_n = None if a.get("all") else int(a.get("top_n") or 10)
        rows = ctx.api.top(s, metric, a.get("flow") or "BOTH", entity, top_n)
        return ToolResult(top_markdown(s, rows, metric, entity, ""), top_structured(rows, metric))

    def run_hold_top(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        entity = api.choice(a.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
        top_n = None if a.get("all") else int(a.get("top_n") or 10)
        rows = ctx.api.hold_top(entity, top_n, _currency(a, ctx), api.search_filters(a))
        return ToolResult(hold_top_markdown(rows, entity), {"rows": rows})

    def run_hold_schedule(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        days = int(a.get("days") or 30)
        rows = ctx.api.hold_schedule(days, _currency(a, ctx), api.search_filters(a))
        return ToolResult(hold_schedule_markdown(rows, days), {"days": days, "rows": rows})

    def run_monitor_total(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        period = window(a.get("date_from"), a.get("date_to"), ctx.today, default_days=1)
        tiles = ctx.api.monitor_total(period, api.int_list(a.get("currency_ids"), "currency_ids", 20),
                                      api.int_list(a.get("card_type_ids"), "card_type_ids", 20),
                                      api.search_filters(a))
        return ToolResult(monitor_total_markdown(period, tiles), {"tiles": monitor_total_structured(tiles)})

    def run_monitor_entity(a: Dict[str, Any]) -> ToolResult:
        ctx = context(a.get("profile"))
        period = window(a.get("date_from"), a.get("date_to"), ctx.today, default_days=1)
        entity = api.choice(a.get("entity_type"), api.ENTITIES, "entity_type")
        operation = api.choice(a.get("operation"), api.MONITOR_TYPES, "operation")
        items = ctx.api.monitor_by_entity(entity, operation, period,
                                          api.int_list(a.get("currency_ids"), "currency_ids", 20),
                                          api.int_list(a.get("card_type_ids"), "card_type_ids", 20),
                                          api.search_filters(a))
        st = monitor_entity_structured(items)
        rows = [[f"{i['id']} · {i['name']}", fmt_count(i["approved"]), fmt_count(i["declined"]),
                 fmt_count(i["filtered"]), fmt_money(i["amount"], i["currency"])] for i in st]
        text = "\n\n".join([f"### {operation.lower()} by {entity.lower()}, {period}",
                            md_table(["Entity", "Approved", "Declined", "Filtered", "Amount"], rows)])
        return ToolResult(text, {"operation": operation, "entity": entity, "items": st})

    monitor_props = {
        "date_from": WINDOW["date_from"], "date_to": WINDOW["date_to"],
        "currency_ids": {"type": "array", "items": {"type": "integer"}, "description": "only these currencies"},
        "card_type_ids": {"type": "array", "items": {"type": "integer"}, "description": "only these card types"},
    }
    top_n = {"type": "integer", "minimum": 1, "maximum": 100, "description": "rows (default 10)"}
    every = {"type": "boolean", "description": "every entity in scope instead of top_n"}

    return [
        Tool("exec_kpi", "Executive KPI for a period against its base: transactions, approved count and amount, "
             "declined, filtered, approval and decline rate, average ticket, and the decline breakdown "
             "(issuer / processor / retryable / 3-D Secure), separately for deposits and withdrawals.",
             schema(_props(WINDOW, FILTER_PROPS)), run_kpi, "Executive KPI"),
        Tool("exec_trend", "Executive trend by day or hour for one metric, the period against its base; with "
             "entity_ids also the selected entities (combined, or one line each with split=SEPARATE).",
             schema(_props(WINDOW, FILTER_PROPS,
                           metric={"type": "string", "enum": list(api.TREND_METRICS)}, flow=FLOW,
                           entity_type=ENTITY,
                           entity_ids={"type": "array", "items": {"type": "integer"},
                                       "description": f"entities to draw (≤ {api.MAX_SELECTED})"},
                           split={"type": "string", "enum": ["COMBINED", "SEPARATE"]}), ["metric"]),
             run_trend, "Executive trend"),
        Tool("exec_top", "Merchants, providers or managers ranked by a metric for the period, with the base value "
             "and the console's direction, approved amount, approval rate and earnings by tier (blueprint rate, "
             "bank cost, manager and reseller margin — shown only to holders of the tariff rights). Ranking by a "
             "margin needs the tariff right of that tier.",
             schema(_props(WINDOW, FILTER_PROPS, metric={"type": "string", "enum": list(api.TOP_METRICS)},
                           flow=FLOW, entity_type=ENTITY, top_n=top_n, all=every), ["metric"]),
             run_top, "Executive top"),
        Tool("exec_hold_top", "Money on hold right now, by merchant, provider or manager: held from merchants and "
             "held by banks. Needs the manager tariff right. An on-demand read; not part of other tools.",
             schema(_props(FILTER_PROPS, entity_type=ENTITY, top_n=top_n, all=every,
                           currency_id=WINDOW["currency_id"])), run_hold_top, "Money on hold"),
        Tool("exec_hold_schedule", "When the money on hold is released, by day from today for `days` days, plus "
             "one row for everything later. Needs the manager tariff right.",
             schema(_props(FILTER_PROPS, days={"type": "integer", "minimum": 1, "maximum": 366},
                           currency_id=WINDOW["currency_id"])), run_hold_schedule, "Hold release schedule"),
        Tool("monitor_total", "Transaction monitor totals by operation type (sale, payout, reversal, chargeback, "
             "fraud, …): approved, declined and filtered counts, amount and approval ratio.",
             schema(_props(monitor_props, FILTER_PROPS)), run_monitor_total, "Transaction monitor"),
        Tool("monitor_by_entity", "Transaction monitor for one operation type, by merchant, manager or provider.",
             schema(_props(monitor_props, FILTER_PROPS, entity_type=ENTITY,
                           operation={"type": "string", "enum": list(api.MONITOR_TYPES)}),
                    ["entity_type", "operation"]), run_monitor_entity, "Transaction monitor by entity"),
    ]
