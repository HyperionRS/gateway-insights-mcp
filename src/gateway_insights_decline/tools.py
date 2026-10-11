# SPDX-License-Identifier: Apache-2.0
"""Tools of the decline server: raw tools over the Decline Dashboard, and business tools built on
them only.

Numbers are the console's: totals, declines, rates, the comparison's excess declines, z-scores and
verdicts. The tools compute only these, named here and in the README:

  change vs reference = segment decline rate − reference decline rate       (percentage points)
  change vs base      = decline rate − base decline rate                    (percentage points)
  declines avoidable  = (segment rate − best rate) × segment transactions   (an estimate, never a promise)
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from gateway_insights_core import entities, report, restrictions, routing
from gateway_insights_core.business import REPORT_PROP, CallRun, finish
from gateway_insights_core.dates import Window
from gateway_insights_core.errors import ApiError, InputError
from gateway_insights_core.markdown import md_escape, md_table
from gateway_insights_core.mcp import Tool, ToolResult, schema
from gateway_insights_core.numbers import fmt_count, fmt_pp, fmt_rate, to_decimal

from . import api
from .api import DeclineApi, Scope

RAW_TOOL_OF_PATH = {api.KPI: "decline_kpi", api.HEATMAP: "decline_heatmap", api.COMPARE: "decline_compare"}

PROFILE = {"type": "string", "description": "configuration profile; default: the configured default"}
FILTER_PROPS = {k: {"type": "array", "items": {"type": "integer"}, "description": f"only these {v.lower()} ids"}
                for k, v in api.FILTERS.items()}
PROPS: Dict[str, Any] = {
    "profile": PROFILE,
    "date_from": {"type": "string", "description": "first bank day, YYYY-MM-DD (default: 7 days to date_to)"},
    "date_to": {"type": "string", "description": "last bank day inclusive, YYYY-MM-DD (default: today)"},
    "base_from": {"type": "string", "description": "first day of the base; with base_to. Default: the same "
                  "number of days right before the period"},
    "base_to": {"type": "string", "description": "last day of the base inclusive; must not overlap the period"},
    "direction": {"type": "string", "enum": list(api.DIRECTIONS),
                  "description": "SRC — the payer's card (default); DST — the recipient's card (payouts, transfers)"},
    **{k: {"type": "array", "items": {"type": "integer"},
           "description": f"card criterion: only these ids (0 = unknown); narrows every breakdown except "
                          f"{api.CRITERION_DIMENSION[k]}"} for k in api.CRITERIA},
    **FILTER_PROPS,
}
DIMENSION = {"type": "string", "enum": list(api.DIMENSIONS)}

CAVEATS = [
    "Counted by bank date, whole days; «total» includes every status, so total − declined is not «approved».",
    "Entity breakdowns need the list right of that entity; a filter without it is dropped by the console "
    "and the result widens.",
]


class Context:
    def __init__(self, decline_api: DeclineApi, today: date) -> None:
        self.api = decline_api
        self.today = today


ContextFn = Callable[[Optional[str]], Context]


def d(row: Dict[str, Any], key: str) -> Optional[Decimal]:
    return to_decimal((row or {}).get(key))


def sub(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[Decimal]:
    return None if a is None or b is None else a - b


def segment(row: Dict[str, Any]) -> str:
    if row.get("others"):
        return str(row.get("dimensionName") or "Others")
    return f"{row.get('dimensionId')} · {row.get('dimensionName') or ''}"


def period(s: Scope) -> Dict[str, str]:
    return {"current": str(s.current), "base": str(s.base)}


def caveats(s: Scope, today: date) -> List[str]:
    out = list(CAVEATS)
    if s.current.end >= today:
        out.append("The period includes today, which is not over yet.")
    if s.base.days != s.current.days:
        out.append("The base is not as long as the period: rates compare, counts do not.")
    return out


# ---- raw tools ------------------------------------------------------------------------------------

def kpi_markdown(s: Scope, k: Dict[str, Any]) -> str:
    rows = [["Selection (with card criteria)", fmt_count(d(k, "criteriaTotalTx")), fmt_count(d(k, "criteriaDeclinedTx")),
             fmt_rate(d(k, "criteriaDeclineRate"))],
            ["Reference (period and entity filters only)", fmt_count(d(k, "referenceTotalTx")),
             fmt_count(d(k, "referenceDeclinedTx")), fmt_rate(d(k, "referenceDeclineRate"))]]
    return "\n\n".join([f"### Decline rate, {s.current} ({s.direction})",
                        md_table(["", "Transactions", "Declined", "Decline rate"], rows),
                        f"Effect of the card criteria: {fmt_pp(sub(d(k, 'criteriaDeclineRate'), d(k, 'referenceDeclineRate')))}."])


def heatmap_markdown(s: Scope, dimension: str, rows: List[Dict[str, Any]], reference: Optional[Decimal] = None) -> str:
    table = [[segment(r), fmt_count(d(r, "totalTx")), fmt_count(d(r, "declinedTx")), fmt_rate(d(r, "declineRate"))]
             + ([fmt_pp(sub(d(r, "declineRate"), reference))] if reference is not None else []) for r in rows]
    cols = ["Segment", "Transactions", "Declined", "Decline rate"] + (["vs reference"] if reference is not None else [])
    return "\n\n".join([f"### Declines by {dimension.lower().replace('_', ' ')}, {s.current}", md_table(cols, table)])


def compare_markdown(s: Scope, dimension: str, data: Dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    head = (f"Verdict: **{md_escape(summary.get('verdict') or 'n/a')}** · decline rate "
            f"{fmt_rate(d(summary, 'baseDeclineRate'))} → {fmt_rate(d(summary, 'declineRate'))} · "
            f"mix effect {fmt_pp(d(summary, 'mixEffect'))}, rate effect {fmt_pp(d(summary, 'rateEffect'))}")
    table = [[segment(r), fmt_rate(d(r, "baseDeclineRate")), fmt_rate(d(r, "declineRate")), fmt_pp(d(r, "deltaRate")),
              fmt_count(d(r, "excessDeclined")), fmt_rate(d(r, "excessShare")), fmt_rate(d(r, "trafficShare")),
              str(d(r, "zScore") if d(r, "zScore") is not None else "—")] for r in data.get("rows") or []]
    return "\n\n".join([f"### {dimension.lower().replace('_', ' ')}: {s.current} against {s.base}", head,
                        md_table(["Segment", "Base rate", "Rate", "Change", "Excess declines", "Share of excess",
                                  "Share of traffic", "z"], table)])


# ---- business tools -------------------------------------------------------------------------------

def _run(ctx: Context) -> CallRun:
    return CallRun(ctx.api.client, RAW_TOOL_OF_PATH)


def overview(ctx: Context, run: CallRun, s: Scope, args: Dict[str, Any], title: str, tool: str) -> Dict[str, Any]:
    min_tx = int(args.get("min_tx") or 30)
    jobs = [lambda: ctx.api.kpi(s), lambda: ctx.api.kpi(s, current=False)]
    jobs += [(lambda dim=dim: ctx.api.heatmap(s, dim, 10, "TOTAL")) for dim in api.CARD_DIMENSIONS]
    now, base, *maps = run.parallel(*jobs)
    rate, base_rate = d(now, "criteriaDeclineRate"), d(base, "criteriaDeclineRate")
    reference = d(now, "referenceDeclineRate")
    spec = report.new_spec(title, tool=tool, period=period(s))
    change = sub(rate, base_rate)
    spec["kpis"] += [
        report.kpi("Decline rate", rate, "rate", base=base_rate, delta=change,
                   direction=None if change is None else (-1 if change > 0 else 1 if change < 0 else 0)),
        report.kpi("Transactions", d(now, "criteriaTotalTx"), "count", base=d(base, "criteriaTotalTx"),
                   delta=sub(d(now, "criteriaTotalTx"), d(base, "criteriaTotalTx"))),
        report.kpi("Declined", d(now, "criteriaDeclinedTx"), "count", base=d(base, "criteriaDeclinedTx"),
                   delta=sub(d(now, "criteriaDeclinedTx"), d(base, "criteriaDeclinedTx"))),
    ]
    spec["summary"].append(f"Decline rate {fmt_rate(rate)}, base {fmt_rate(base_rate)} ({fmt_pp(change)}).")
    worst = []
    for dim, rows in zip(api.CARD_DIMENSIONS, maps):
        for r in rows:
            if r.get("others") or (d(r, "totalTx") or 0) < min_tx:
                continue
            if (sub(d(r, "declineRate"), reference) or 0) <= 0:   # only segments above the average
                continue
            worst.append({"dimension": dim.lower().replace("_", " "), "segment": segment(r), "tx": d(r, "totalTx"),
                          "rate": d(r, "declineRate"), "vs": sub(d(r, "declineRate"), reference)})
    worst.sort(key=lambda w: -(w["vs"] or Decimal(-1)))
    spec["sections"].append({
        "title": "Card segments that decline most above the average",
        "text": f"The largest segments of each card dimension with at least {min_tx} transactions, by how far "
                "their decline rate is above the reference.",
        "table": report.table([{"key": "dimension", "label": "Dimension", "unit": "text"},
                               {"key": "segment", "label": "Segment", "unit": "text"},
                               {"key": "tx", "label": "Transactions", "unit": "count"},
                               {"key": "rate", "label": "Decline rate", "unit": "rate"},
                               {"key": "vs", "label": "vs reference", "unit": "pp"}], worst[:15])})
    spec["caveats"] += caveats(s, ctx.today)
    return spec


def change_tool(ctx: Context, run: CallRun, s: Scope, args: Dict[str, Any]) -> Dict[str, Any]:
    dims = [api.choice(x, api.DIMENSIONS, "dimensions") for x in (args.get("dimensions") or api.CARD_DIMENSIONS)]
    if len(dims) > 8:
        raise InputError("dimensions: at most 8")
    results = run.parallel(*[(lambda dim=dim: ctx.api.compare(s, dim, 5, "EXCESS")) for dim in dims])
    spec = report.new_spec("What changed in declines", tool="decline_change", period=period(s))
    verdict_rows = []
    card_verdicts = []
    concentrated = []
    for dim, data in zip(dims, results):
        sm = data.get("summary") or {}
        verdict = sm.get("verdict") or "NO_BASE"
        if dim in api.CARD_DIMENSIONS:
            card_verdicts.append(verdict)
        if verdict == "CONCENTRATED":
            concentrated.append(f"{dim.lower().replace('_', ' ')} → {sm.get('topDimensionName') or '?'} "
                                f"({fmt_rate(d(sm, 'topExcessShare'))} of the excess declines)")
        verdict_rows.append({"dimension": dim.lower().replace("_", " "), "verdict": verdict,
                             "top": sm.get("topDimensionName") or "—", "top_share": d(sm, "topExcessShare"),
                             "mix": d(sm, "mixEffect"), "rate": d(sm, "rateEffect")})
        spec["sections"].append({"title": f"{dim.lower().replace('_', ' ')}: segments with the most excess declines",
                                 "table": report.table(
            [{"key": "segment", "label": "Segment", "unit": "text"},
             {"key": "base", "label": "Base rate", "unit": "rate"}, {"key": "rate", "label": "Rate", "unit": "rate"},
             {"key": "excess", "label": "Excess declines", "unit": "count"},
             {"key": "share", "label": "Share of excess", "unit": "rate"}, {"key": "z", "label": "z", "unit": "number"}],
            [{"segment": segment(r), "base": d(r, "baseDeclineRate"), "rate": d(r, "declineRate"),
              "excess": d(r, "excessDeclined"), "share": d(r, "excessShare"), "z": d(r, "zScore")}
             for r in data.get("rows") or []])})
    first = results[0].get("summary") or {}
    spec["kpis"].append(report.kpi("Decline rate", d(first, "declineRate"), "rate", base=d(first, "baseDeclineRate"),
                                   delta=sub(d(first, "declineRate"), d(first, "baseDeclineRate"))))
    spec["sections"].insert(0, {"title": "Verdict by dimension", "table": report.table(
        [{"key": "dimension", "label": "Dimension", "unit": "text"}, {"key": "verdict", "label": "Verdict", "unit": "text"},
         {"key": "top", "label": "Largest contributor", "unit": "text"},
         {"key": "top_share", "label": "Its share of excess", "unit": "rate"},
         {"key": "mix", "label": "Mix effect", "unit": "pp"}, {"key": "rate", "label": "Rate effect", "unit": "pp"}],
        verdict_rows)})
    if card_verdicts and all(v == "BROAD" for v in card_verdicts):
        spec["summary"].append("The rise is broad in every card dimension: the cause is unlikely to be the cards — "
                               "look at the provider, the terminal, the rules.")
    if concentrated:
        spec["summary"].append("Concentrated: " + "; ".join(concentrated) + ".")
    if any(v == "MIX" for v in card_verdicts):
        spec["summary"].append("Part of the rise is a change of traffic mix, not worse approval within segments.")
    spec["caveats"] += caveats(s, ctx.today) + [
        "Verdicts are the console's heuristic (concentrated: one segment gives over half of the excess and over 1.5× "
        "its traffic share; mix: most of the rise comes from the traffic mix), not a statistical test."]
    return spec


def where(ctx: Context, run: CallRun, s: Scope, args: Dict[str, Any]) -> Dict[str, Any]:
    dim = api.choice(args.get("dimension"), api.DIMENSIONS, "dimension")
    top_n = int(args.get("top_n") or 15)
    k, rows = run.parallel(lambda: ctx.api.kpi(s), lambda: ctx.api.heatmap(s, dim, top_n, "DECLINED"))
    reference = d(k, "referenceDeclineRate")
    spec = report.new_spec(f"Where declines come from: {dim.lower().replace('_', ' ')}", tool="decline_where",
                           period=period(s))
    spec["kpis"] += [report.kpi("Decline rate of the selection", d(k, "criteriaDeclineRate"), "rate"),
                     report.kpi("Reference", reference, "rate")]
    spec["sections"].append({"title": f"By {dim.lower().replace('_', ' ')}, most declines first", "table": report.table(
        [{"key": "segment", "label": "Segment", "unit": "text"}, {"key": "tx", "label": "Transactions", "unit": "count"},
         {"key": "declined", "label": "Declined", "unit": "count"}, {"key": "rate", "label": "Decline rate", "unit": "rate"},
         {"key": "vs", "label": "vs reference", "unit": "pp"}],
        [{"segment": segment(r), "tx": d(r, "totalTx"), "declined": d(r, "declinedTx"), "rate": d(r, "declineRate"),
          "vs": sub(d(r, "declineRate"), reference)} for r in rows])})
    param = next((p for p, x in api.CRITERION_DIMENSION.items() if x == dim), api.FILTER_OF_DIMENSION.get(dim))
    if param:
        spec["summary"].append(f"To go one level deeper, pass a segment id as {param} and pick the next dimension.")
    spec["caveats"] += caveats(s, ctx.today)
    return spec


def route(ctx: Context, run: CallRun, s: Scope, args: Dict[str, Any]) -> Dict[str, Any]:
    if not s.criteria:
        raise InputError("give the card segment: at least one of " + ", ".join(api.CRITERIA))
    dim = api.choice(args.get("dimension"), ("PROVIDER", "BANK_TERMINAL"), "dimension", "PROVIDER")
    min_tx = int(args.get("min_tx") or 30)
    k, rows = run.parallel(lambda: ctx.api.kpi(s), lambda: ctx.api.heatmap(s, dim, 50, "TOTAL"))
    usable = [r for r in rows if not r.get("others") and (d(r, "totalTx") or 0) >= min_tx
              and d(r, "declineRate") is not None]
    usable.sort(key=lambda r: d(r, "declineRate"))
    spec = report.new_spec(f"Where this card segment passes best, by {dim.lower().replace('_', ' ')}",
                           tool="decline_route", period=period(s))
    spec["kpis"] += [report.kpi("Segment decline rate", d(k, "criteriaDeclineRate"), "rate"),
                     report.kpi("All traffic", d(k, "referenceDeclineRate"), "rate")]
    if usable:
        best = d(usable[0], "declineRate")
        spec["summary"].append(f"Best: {segment(usable[0])} at {fmt_rate(best)}; worst: {segment(usable[-1])} at "
                               f"{fmt_rate(d(usable[-1], 'declineRate'))}, among those with ≥ {min_tx} transactions.")
        table = []
        for r in usable:
            rate, tx = d(r, "declineRate"), d(r, "totalTx")
            table.append({"segment": segment(r), "tx": tx, "rate": rate, "vs_best": sub(rate, best),
                          "avoidable": (rate - best) * tx if rate is not None and tx is not None else None})
        spec["sections"].append({"title": "Decline rate of this segment by route, best first", "table": report.table(
            [{"key": "segment", "label": dim.replace("_", " ").capitalize(), "unit": "text"},
             {"key": "tx", "label": "Transactions", "unit": "count"},
             {"key": "rate", "label": "Decline rate", "unit": "rate"},
             {"key": "vs_best", "label": "vs best", "unit": "pp"},
             {"key": "avoidable", "label": "Declines avoidable at the best rate", "unit": "number"}], table)})
    else:
        spec["summary"].append(f"No {dim.lower().replace('_', ' ')} carried at least {min_tx} transactions of this "
                               "segment in the period.")
    spec["caveats"] += caveats(s, ctx.today) + [
        "«Avoidable» = (rate − best rate) × transactions: an upper estimate if this traffic moved, not a promise — "
        "routes differ in more than cards (amounts, merchants, 3-D Secure)."]
    return spec


def opportunity(ctx: Context, run: CallRun, s: Scope, args: Dict[str, Any]) -> Dict[str, Any]:
    dim = api.choice(args.get("dimension"), api.DIMENSIONS, "dimension", "BANK")
    top_n = int(args.get("top_n") or 10)
    data = run.call(ctx.api.compare, s, dim, top_n, "EXCESS")
    sm = data.get("summary") or {}
    rows = [r for r in data.get("rows") or [] if not r.get("others")]
    positive = [d(r, "excessDeclined") for r in rows if (d(r, "excessDeclined") or 0) > 0]
    spec = report.new_spec(f"Excess declines by {dim.lower().replace('_', ' ')}", tool="decline_opportunity",
                           period=period(s))
    spec["kpis"] += [report.kpi("Excess declines, all segments", d(sm, "excessDeclined"), "count"),
                     report.kpi(f"In the top {len(rows)} segments", sum(positive, Decimal(0)), "count"),
                     report.kpi("Decline rate", d(sm, "declineRate"), "rate", base=d(sm, "baseDeclineRate"),
                                delta=sub(d(sm, "declineRate"), d(sm, "baseDeclineRate")))]
    spec["summary"].append(f"Returning the top {len(rows)} segments to their base rates would avoid about "
                           f"{fmt_count(sum(positive, Decimal(0)))} declines (the console's excess over each segment's "
                           "own base rate).")
    spec["sections"].append({"title": "Segments with the most excess declines", "table": report.table(
        [{"key": "segment", "label": "Segment", "unit": "text"}, {"key": "tx", "label": "Transactions", "unit": "count"},
         {"key": "base", "label": "Base rate", "unit": "rate"}, {"key": "rate", "label": "Rate", "unit": "rate"},
         {"key": "excess", "label": "Excess declines", "unit": "count"}, {"key": "z", "label": "z", "unit": "number"}],
        [{"segment": segment(r), "tx": d(r, "totalTx"), "base": d(r, "baseDeclineRate"), "rate": d(r, "declineRate"),
          "excess": d(r, "excessDeclined"), "z": d(r, "zScore")} for r in rows])})
    spec["caveats"] += caveats(s, ctx.today) + [
        "A segment with fewer than 30 base transactions is measured against the overall base rate.",
        "A large z on a large segment can be volume alone; a jump in a segment that barely declined before says more."]
    return spec


# ---- registration ---------------------------------------------------------------------------------

def build(context: ContextFn) -> List[Tool]:
    def raw(name, title, description, fn, extra=None, required=None):
        props = dict(PROPS)
        props.update(extra or {})

        def handler(a):
            ctx = context(a.get("profile"))
            return fn(ctx, api.scope(a, ctx.today), a)
        return Tool(name, description, schema(props, required), handler, title)

    def business(name, title, description, fn, extra=None, required=None):
        props = dict(PROPS)
        props.update(REPORT_PROP)
        props.update(extra or {})

        def handler(a):
            ctx = context(a.get("profile"))
            run = _run(ctx)
            return finish(run, fn(ctx, run, api.scope(a, ctx.today), a), a)
        return Tool(name, description, schema(props, required), handler, title)

    def kpi_tool(ctx, s, a):
        k = ctx.api.kpi(s)
        return ToolResult(kpi_markdown(s, k), {"period": str(s.current), "kpi": k})

    def heatmap_tool(ctx, s, a):
        dim = api.choice(a.get("dimension"), api.DIMENSIONS, "dimension")
        rows = ctx.api.heatmap(s, dim, int(a.get("top_n") or 10), a.get("sort") or "DECLINED")
        return ToolResult(heatmap_markdown(s, dim, rows), {"dimension": dim, "rows": rows})

    def compare_tool(ctx, s, a):
        dim = api.choice(a.get("dimension"), api.DIMENSIONS, "dimension")
        data = ctx.api.compare(s, dim, int(a.get("top_n") or 10), a.get("sort") or "EXCESS")
        return ToolResult(compare_markdown(s, dim, data), {"dimension": dim, **data})

    def check_tool(ctx, s, a):
        profile = ctx.api.client.profile
        lines = [f"- profile: {md_escape(profile.name)} → {md_escape(profile.base_url)}",
                 f"- token: {md_escape(profile.token_hint)}",
                 f"- TLS verification: {'on' if profile.verify_tls else 'OFF'}",
                 f"- theme: {md_escape(profile.theme or 'neutral (built in)')}",
                 f"- reports: {md_escape(profile.report_dir)}"]
        y = ctx.today - timedelta(days=1)
        probe = Scope(Window(y, y), Window(y, y), False, "SRC", {}, [])
        try:
            k = ctx.api.kpi(probe)
            lines.append(f"- probe (decline KPI for {y}): ok, {fmt_count(d(k, 'referenceTotalTx'))} transactions")
        except ApiError as exc:
            lines.append(f"- probe (decline KPI for {y}): {md_escape(exc)}")
        return ToolResult("### Connection check\n\n" + "\n".join(lines))

    top_n = {"type": "integer", "minimum": 1, "maximum": 100}
    min_tx = {"type": "integer", "minimum": 1, "description": "ignore segments with fewer transactions (default 30)"}
    return [
        Tool("decline_check", "Check the connection: profile, console address, token hint, TLS, the report theme and "
             "folder, and a probe read.",
             schema({"profile": PROFILE}), lambda a: check_tool(context(a.get("profile")), None, a), "Connection check"),
        entities.tool(lambda p: context(p).api.client, FILTER_PROPS, api.search_filters, PROFILE),
        routing.tool(lambda p: context(p).api.client, PROFILE),
        restrictions.tool(lambda p: context(p).api.client, PROFILE),
        raw("decline_kpi", "Decline KPI",
            "Decline rate for the period: the selection with card criteria against the reference without them.",
            kpi_tool),
        raw("decline_heatmap", "Decline breakdown",
            "Declines by one dimension — a card characteristic (country, bank, funding source, card type, product) "
            "or an entity (requestor, blueprint, merchant, manager, bank terminal, provider): top segments plus "
            "«others».", heatmap_tool,
            {"dimension": DIMENSION, "top_n": top_n, "sort": {"type": "string", "enum": list(api.HEATMAP_SORTS)}},
            ["dimension"]),
        raw("decline_compare", "Decline comparison",
            "The period against its base by one dimension: per segment base and current rate, excess declines, "
            "z-score, and the console's verdict (no base, single, flat, mix, concentrated, broad).", compare_tool,
            {"dimension": DIMENSION, "top_n": top_n, "sort": {"type": "string", "enum": list(api.COMPARE_SORTS)}},
            ["dimension"]),
        business("decline_overview", "Decline overview",
                 "The decline rate against the base and the card segments that decline most above the average.",
                 lambda c, r, s, a: overview(c, r, s, a, "Decline overview", "decline_overview"), {"min_tx": min_tx}),
        business("decline_change", "What changed",
                 "Why the decline rate moved: per dimension the console's verdict (concentrated, mix, broad, …) and "
                 "the segments with the most excess declines. Broad in every card dimension means the cause is "
                 "above the cards.", change_tool,
                 {"dimensions": {"type": "array", "items": DIMENSION, "maxItems": 8}}),
        business("decline_where", "Where declines come from",
                 "Drill down: declines by one dimension, within the card criteria given (country → bank → product).",
                 where, {"dimension": DIMENSION, "top_n": top_n}, ["dimension"]),
        business("decline_route", "Best route for a card segment",
                 "For a card segment (criteria required), which provider or bank terminal declines it least, and how "
                 "many declines each route would avoid at the best rate.", route,
                 {"dimension": {"type": "string", "enum": ["PROVIDER", "BANK_TERMINAL"]}, "min_tx": min_tx}),
        business("decline_opportunity", "Excess declines",
                 "Where the excess declines against the base sit, by one dimension, and how many returning those "
                 "segments to their base rates would avoid.", opportunity, {"dimension": DIMENSION, "top_n": top_n}),
        business("decline_payouts", "Payout declines",
                 "The decline overview for the recipient's card (payouts and transfers).",
                 lambda c, r, s, a: overview(c, r, Scope(s.current, s.base, s.base_explicit, "DST", s.criteria,
                                                         s.filters), a, "Payout decline overview",
                                             "decline_payouts"), {"min_tx": min_tx}),
    ]
