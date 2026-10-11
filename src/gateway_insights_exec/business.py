# SPDX-License-Identifier: Apache-2.0
"""Business tools of the executive server: answers to the owner's questions.

Every tool reads the console only through the raw layer (ExecApi), within a call budget, and
returns a report spec. Numbers are the console's; the few the tools compute themselves are named
next to their code and listed with their formulas in the README:

  change            = now − base
  relative change   = (now − base) / |base|
  share of gains    = a growing entity's change / the sum of all growth (falls likewise, over all falls)
  share             = an entity's value / the total of the rows it is part of; n/a when the total is ≤ 0
  effective rate    = margin / approved amount
  concentration     = the share of the top 1, 5, 10 entities; HHI = Σ share² over entities with value > 0
  released within N = Σ hold released on days before today + N
  chargeback ratio  = chargebacks / approved sales, counts
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from gateway_insights_core import report
from gateway_insights_core.errors import InputError
from gateway_insights_core.mcp import Tool, ToolResult, schema
from gateway_insights_core.numbers import fmt_money, fmt_pp, fmt_rate, ratio, to_decimal

from . import api
from .business_base import (KPI_CAVEATS, MARGIN_CAVEAT, Run, business_props, d, direction, entity_label, finish,
                            hidden, rel_change, split_total, sub)
from .raw_tools import ENTITY, FLOW, ToolContext

Context = Callable[[Optional[str]], ToolContext]
TIERS = ("MANAGER", "RESELLER", "TOTAL")
OTHER = {"MERCHANT": "PROVIDER", "PROVIDER": "MERCHANT", "MANAGER": "MERCHANT"}
FILTER_OF = {"MERCHANT": "merchant_ids", "PROVIDER": "provider_ids", "MANAGER": "manager_ids"}


def _scope(run_args: Dict[str, Any], ctx: ToolContext) -> api.Scope:
    args = dict(run_args)
    args["grain"] = "DAY"
    return api.scope(args, ctx.today, ctx.currency_id)


def _currency(kpi: Dict[str, Any]) -> str:
    return str(kpi.get("displayCurrency") or "")


def tier_margin(row: Dict[str, Any], tier: str, base: bool = False) -> Optional[Decimal]:
    """The tier's margin from a top row; None when the console hid it. Total = manager + reseller,
    and is None if either is hidden — a hidden tier is never filled in."""
    suffix = "Prev" if base else ""
    manager, reseller = d(row, "flowManagerMargin" + suffix), d(row, "flowResellerMargin" + suffix)
    if tier == "MANAGER":
        return manager
    if tier == "RESELLER":
        return reseller
    return None if manager is None or reseller is None else manager + reseller


def share(value: Optional[Decimal], total: Optional[Decimal]) -> Optional[Decimal]:
    """value / total; n/a when the total is not positive — a share of a loss reads backwards."""
    if total is None or total <= 0:
        return None
    return ratio(value, total)


# ---- ceo_briefing ---------------------------------------------------------------------------------

BRIEF_TILES = [
    ("Deposits approved amount", "depositApprovedAmount", "money"),
    ("Withdrawals approved amount", "withdrawalApprovedAmount", "money"),
    ("Deposit approval rate", "depositApprovalRate", "rate"),
    ("Withdrawal approval rate", "withdrawalApprovalRate", "rate"),
    ("Deposits declined", "depositDeclined", "count"),
    ("Deposits filtered", "depositFiltered", "count"),
]
DELTA_KEY = {"depositApprovalRate": "depositApprovalRateDeltaPp", "withdrawalApprovalRate": "withdrawalApprovalRateDeltaPp"}
WATCH_TYPES = ("CHARGEBACK", "FRAUD", "REVERSAL", "CHARGEBACK_REVERSAL", "PREARBITRATION", "ARBITRATION")


def movers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Entities with change = now − base and contribution = the change's share of all growth (for a
    growing entity) or of all falls (for a falling one). Dividing by the net change instead mixes signs:
    one large fall makes every gain read as a negative share."""
    entities, _ = split_total(rows)
    items = []
    for r in entities:
        now, base = d(r, "metricValue") or Decimal(0), d(r, "metricValuePrev") or Decimal(0)
        items.append({"row": r, "now": now, "base": base, "change": now - base})
    gains = sum((i["change"] for i in items if i["change"] > 0), Decimal(0))
    falls = sum((i["change"] for i in items if i["change"] < 0), Decimal(0))
    for i in items:
        i["contribution"] = (ratio(i["change"], gains) if i["change"] > 0
                             else ratio(i["change"], falls) if i["change"] < 0 else Decimal(0))
    return items


def briefing(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    s = run.scope
    kpi, merchants, providers = run.parallel(
        lambda: run.api.kpi(s),
        lambda: run.api.top(s, "AMOUNT", "BOTH", "MERCHANT", None),
        lambda: run.api.top(s, "DECLINE", "DEPOSIT", "PROVIDER", 10))
    tiles = run.call(run.api.monitor_total, s.current, (), (), s.filters)
    cur = _currency(kpi)
    spec = report.new_spec("Business briefing", tool="ceo_briefing", period=run.period(), currency=cur)

    bad = []
    for label, key, unit in BRIEF_TILES:
        delta = d(kpi, DELTA_KEY.get(key, key + "Delta"))
        dirn = direction(kpi.get(key + "Dir"))
        if dirn == -1:
            bad.append(label.lower())
        spec["kpis"].append(report.kpi(label, d(kpi, key), unit, base=d(kpi, key + "Prev"), delta=delta,
                                       direction=dirn))
    amount = d(kpi, "depositApprovedAmount")
    pct = d(kpi, "depositApprovedAmountDeltaPct")
    spec["summary"].append(f"Deposits approved {fmt_money(amount, cur)}"
                           + (f", {'+' if pct and pct > 0 else ''}{fmt_rate(pct)} to the base." if pct is not None
                              else "."))
    spec["summary"].append("Moved the bad way: " + ", ".join(bad) + "." if bad
                           else "Nothing moved the bad way in the headline numbers.")

    items = movers(merchants)
    gainers = sorted([i for i in items if i["change"] > 0], key=lambda i: -i["change"])[:5]
    losers = sorted([i for i in items if i["change"] < 0], key=lambda i: i["change"])[:5]
    if losers:
        worst = losers[0]
        spec["summary"].append(f"Largest fall: {entity_label(worst['row'])}, "
                               f"{fmt_money(worst['change'], cur)} to the base.")
    cols = [{"key": "entity", "label": "Merchant", "unit": "text"},
            {"key": "now", "label": "Approved amount", "unit": "money"},
            {"key": "base", "label": "Base", "unit": "money"},
            {"key": "change", "label": "Change", "unit": "money"},
            {"key": "contribution", "label": "Share of all gains / falls", "unit": "rate"}]
    for title, group in (("Merchants that added the most", gainers), ("Merchants that lost the most", losers)):
        spec["sections"].append({"title": title, "table": report.table(cols, [
            {"entity": entity_label(i["row"]), "now": i["now"], "base": i["base"], "change": i["change"],
             "contribution": i["contribution"]} for i in group])})

    prov_rows, _ = split_total(providers)
    spec["sections"].append({"title": "Providers with the highest deposit decline rate", "table": report.table(
        [{"key": "entity", "label": "Provider", "unit": "text"},
         {"key": "rate", "label": "Decline rate", "unit": "rate"},
         {"key": "base", "label": "Base", "unit": "rate"},
         {"key": "change", "label": "Change", "unit": "pp"},
         {"key": "issuer", "label": "Issuer share of declines", "unit": "rate"},
         {"key": "processor", "label": "Processor share of declines", "unit": "rate"}],
        [{"entity": entity_label(r), "rate": d(r, "flowDeclineRate"), "base": d(r, "flowDeclineRatePrev"),
          "change": sub(d(r, "flowDeclineRate"), d(r, "flowDeclineRatePrev")),
          "issuer": d(r, "flowIssuerPct"), "processor": d(r, "flowProcessorPct")} for r in prov_rows])})

    watch = [t for t in tiles if t.get("type") in WATCH_TYPES]
    if watch:
        spec["sections"].append({"title": "Chargebacks, fraud and reversals", "table": report.table(
            [{"key": "type", "label": "Operation", "unit": "text"},
             {"key": "count", "label": "Count", "unit": "count"},
             {"key": "amount", "label": "Amount", "unit": "text"}],
            [{"type": t.get("type"), "count": to_decimal(t.get("approvedCountFormatted")),
              "amount": fmt_money(to_decimal(t.get("amountFormatted")), t.get("amountCurrencyCode") or "")}
             for t in watch])})
    spec["caveats"] += KPI_CAVEATS + run.standard_caveats() + [
        "Chargebacks, fraud and reversals are counted by status-change date; the KPI is by bank date."]
    return spec


# ---- ceo_earnings ---------------------------------------------------------------------------------

def earnings(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    s = run.scope
    tier = api.choice(args.get("tier"), TIERS, "tier", "MANAGER")
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
    flow = api.choice(args.get("flow"), api.FLOWS, "flow", "BOTH")
    top_n = int(args.get("top_n") or 10)
    kpi, by_margin, by_amount = run.parallel(
        lambda: run.api.kpi(s),
        lambda: run.api.top(s, f"{tier}_MARGIN", flow, entity, top_n),
        lambda: run.api.top(s, "AMOUNT", flow, entity, max(top_n, 20)))
    cur = _currency(kpi)
    label = {"MANAGER": "Manager margin", "RESELLER": "Reseller margin", "TOTAL": "Manager + reseller margin"}[tier]
    spec = report.new_spec(f"Earnings by {entity.lower()}", tool="ceo_earnings", period=run.period(), currency=cur)
    rows, total = split_total(by_margin)

    margin, margin_base = tier_margin(total, tier), tier_margin(total, tier, base=True)
    amount, amount_base = d(total, "flowApprovedAmount"), d(total, "flowApprovedAmountPrev")
    eff, eff_base = ratio(margin, amount), ratio(margin_base, amount_base)
    change = sub(margin, margin_base)
    spec["kpis"] += [
        report.kpi(label, hidden(margin), "money", base=hidden(margin_base), delta=change,
                   direction=direction(change)),
        report.kpi("Approved amount", amount, "money", base=amount_base, delta=sub(amount, amount_base),
                   direction=direction(sub(amount, amount_base))),
        report.kpi("Effective rate (margin / approved amount)", eff, "rate", base=eff_base,
                   delta=sub(eff, eff_base), direction=direction(sub(eff, eff_base))),
    ]
    if margin is not None:
        rel = rel_change(margin, margin_base)
        spec["summary"].append(f"{label} {fmt_money(margin, cur)}" + (f", {fmt_rate(rel)} to the base." if rel is not None
                                                                       else "."))
        top3 = sum((tier_margin(r, tier) or Decimal(0)) for r in rows[:3])
        if margin > 0 and rows:
            spec["summary"].append(f"The top three {entity.lower()}s give {fmt_rate(share(top3, margin))} of it.")

    table_rows, cats, now_vals, base_vals = [], [], [], []
    for r in rows:
        m, mb = tier_margin(r, tier), tier_margin(r, tier, base=True)
        a = d(r, "flowApprovedAmount")
        table_rows.append({"entity": entity_label(r), "margin": hidden(m), "base": hidden(mb), "change": sub(m, mb),
                           "share": share(m, margin), "amount": a, "rate": ratio(m, a)})
        cats.append(entity_label(r))
        now_vals.append(report.cell(m) if m is not None else None)
        base_vals.append(report.cell(mb) if mb is not None else None)
    spec["sections"].append({
        "title": f"Top {entity.lower()}s by {label.lower()}",
        "chart": {"type": "bar", "unit": "money", "categories": cats,
                  "series": [{"label": "period", "values": now_vals}, {"label": "base", "values": base_vals}]},
        "table": report.table([
            {"key": "entity", "label": entity.capitalize(), "unit": "text"},
            {"key": "margin", "label": label, "unit": "money"}, {"key": "base", "label": "Base", "unit": "money"},
            {"key": "change", "label": "Change", "unit": "money"},
            {"key": "share", "label": "Share of the total", "unit": "rate"},
            {"key": "amount", "label": "Approved amount", "unit": "money"},
            {"key": "rate", "label": "Effective rate", "unit": "rate"}], table_rows)})

    low = []
    if eff is not None:
        for r in split_total(by_amount)[0]:
            m, a = tier_margin(r, tier), d(r, "flowApprovedAmount")
            r_eff = ratio(m, a)
            if r_eff is not None and r_eff < eff:
                low.append({"entity": entity_label(r), "amount": a, "margin": m, "rate": r_eff})
    if low:
        spec["sections"].append({
            "title": "Large volume, margin below the average rate",
            "text": f"Among the {max(top_n, 20)} largest by approved amount, these earn less per unit of volume than "
                    f"the average {fmt_rate(eff)}.",
            "table": report.table([
                {"key": "entity", "label": entity.capitalize(), "unit": "text"},
                {"key": "amount", "label": "Approved amount", "unit": "money"},
                {"key": "margin", "label": label, "unit": "money"},
                {"key": "rate", "label": "Effective rate", "unit": "rate"}], low)})
    spec["caveats"] += [MARGIN_CAVEAT] + run.standard_caveats()
    return spec


# ---- ceo_movers -----------------------------------------------------------------------------------

MOVER_METRICS = ("AMOUNT", "APPROVAL", "MANAGER_MARGIN", "RESELLER_MARGIN", "TOTAL_MARGIN")


def classify(rows: List[Dict[str, Any]], min_change: Decimal) -> Dict[str, List[Dict[str, Any]]]:
    """gained/lost: |change| ≥ min_change; new: base = 0 and now > 0; left: now = 0 and base > 0."""
    out: Dict[str, List[Dict[str, Any]]] = {"new": [], "left": [], "gained": [], "lost": []}
    for i in movers(rows):
        if i["base"] == 0 and i["now"] > 0:
            out["new"].append(i)
        elif i["now"] == 0 and i["base"] > 0:
            out["left"].append(i)
        elif i["change"] >= min_change and i["change"] > 0:
            out["gained"].append(i)
        elif -i["change"] >= min_change and i["change"] < 0:
            out["lost"].append(i)
    out["gained"].sort(key=lambda i: -i["change"])
    out["lost"].sort(key=lambda i: i["change"])
    out["new"].sort(key=lambda i: -i["now"])
    out["left"].sort(key=lambda i: -i["base"])
    return out


def movers_tool(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    s = run.scope
    metric = api.choice(args.get("metric"), MOVER_METRICS, "metric", "AMOUNT")
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
    n = max(1, min(int(args.get("n") or 10), 50))
    try:
        min_change = to_decimal(args.get("min_change")) or Decimal(0)
    except ValueError:
        raise InputError("min_change must be a number") from None
    kpi, rows = run.parallel(lambda: run.api.kpi(s),
                             lambda: run.api.top(s, metric, api.choice(args.get("flow"), api.FLOWS, "flow", "BOTH"),
                                                 entity, None))
    cur = _currency(kpi)
    unit = "count" if metric == "APPROVAL" else "money"
    groups = classify(rows, min_change)
    spec = report.new_spec(f"Who grew and who fell: {entity.lower()}s by {metric.lower().replace('_', ' ')}",
                           tool="ceo_movers", period=run.period(), currency=cur)
    spec["summary"].append(f"{len(groups['gained'])} grew, {len(groups['lost'])} fell, "
                           f"{len(groups['new'])} new, {len(groups['left'])} left (no activity in the period).")
    cols = [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
            {"key": "now", "label": "Period", "unit": unit}, {"key": "base", "label": "Base", "unit": unit},
            {"key": "change", "label": "Change", "unit": unit},
            {"key": "contribution", "label": "Share of all gains / falls", "unit": "rate"}]
    for key, title in (("gained", "Grew the most"), ("lost", "Fell the most"), ("new", "New: nothing in the base"),
                       ("left", "Left: nothing in the period")):
        if groups[key]:
            spec["sections"].append({"title": title, "table": report.table(cols, [
                {"entity": entity_label(i["row"]), "now": i["now"], "base": i["base"], "change": i["change"],
                 "contribution": i["contribution"]} for i in groups[key][:n]])})
    if metric.endswith("MARGIN"):
        spec["caveats"].append(MARGIN_CAVEAT)
    spec["caveats"] += run.standard_caveats() + [
        "«Left» means no activity in the period, not that the entity was closed."]
    return spec


# ---- ceo_concentration ----------------------------------------------------------------------------

CONC_METRICS = ("AMOUNT", "MANAGER_MARGIN", "TOTAL_MARGIN")


def concentration(values: List[Decimal]) -> Dict[str, Optional[Decimal]]:
    """Shares of the top 1/5/10 and HHI = Σ share², over values > 0 only."""
    positive = sorted((v for v in values if v > 0), reverse=True)
    total = sum(positive, Decimal(0))
    if total == 0:
        return {"top1": None, "top5": None, "top10": None, "hhi": None, "entities": Decimal(len(positive))}
    shares = [v / total for v in positive]
    return {"top1": sum(shares[:1], Decimal(0)), "top5": sum(shares[:5], Decimal(0)),
            "top10": sum(shares[:10], Decimal(0)), "hhi": sum((x * x for x in shares), Decimal(0)),
            "entities": Decimal(len(positive))}


def concentration_tool(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    s = run.scope
    metric = api.choice(args.get("metric"), CONC_METRICS, "metric", "AMOUNT")
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
    kpi, rows = run.parallel(lambda: run.api.kpi(s), lambda: run.api.top(s, metric, "BOTH", entity, None))
    entities, _ = split_total(rows)
    now = concentration([d(r, "metricValue") or Decimal(0) for r in entities])
    base = concentration([d(r, "metricValuePrev") or Decimal(0) for r in entities])
    spec = report.new_spec(f"Concentration of {metric.lower().replace('_', ' ')} by {entity.lower()}",
                           tool="ceo_concentration", period=run.period(), currency=_currency(kpi))
    for key, label in (("top1", "Share of the largest"), ("top5", "Share of the top 5"),
                       ("top10", "Share of the top 10"), ("hhi", "HHI (Σ share²)")):
        spec["kpis"].append(report.kpi(label, now[key], "rate" if key != "hhi" else "number", base=base[key],
                                       delta=sub(now[key], base[key])))
    spec["kpis"].append(report.kpi(f"{entity.capitalize()}s with activity", now["entities"], "count",
                                   base=base["entities"], delta=sub(now["entities"], base["entities"])))
    if now["top5"] is not None:
        spec["summary"].append(f"The top 5 {entity.lower()}s give {fmt_rate(now['top5'])}; "
                               f"the largest alone {fmt_rate(now['top1'])}.")
    ranked = sorted(entities, key=lambda r: -(d(r, "metricValue") or Decimal(0)))[:10]
    total_now = sum((v for v in (d(r, "metricValue") for r in entities) if v and v > 0), Decimal(0))
    spec["sections"].append({"title": f"The 10 largest {entity.lower()}s", "table": report.table(
        [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
         {"key": "value", "label": "Period", "unit": "money"},
         {"key": "share", "label": "Share", "unit": "rate"}],
        [{"entity": entity_label(r), "value": d(r, "metricValue"), "share": share(d(r, "metricValue"), total_now)}
         for r in ranked])})
    spec["caveats"] += ["Shares and HHI count only entities with a positive value; a negative margin is left out.",
                        "HHI runs from near 0 (spread out) to 1 (one entity has everything)."]
    if metric.endswith("MARGIN"):
        spec["caveats"].append(MARGIN_CAVEAT)
    spec["caveats"] += run.standard_caveats()
    return spec


# ---- ceo_holds ------------------------------------------------------------------------------------

def released_within(rows: List[Dict[str, Any]], today, days: int, key: str) -> Decimal:
    """Σ released on days strictly before today + days (the «later» row is not counted)."""
    limit = int((today + timedelta(days=days)).strftime("%Y%m%d"))
    total = Decimal(0)
    for r in rows:
        day = r.get("releaseDay")
        if day in (None, ""):
            continue
        if int(day) < limit:
            total += d(r, key) or Decimal(0)
    return total


def holds(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
    days = max(1, min(int(args.get("days") or 90), 366))
    currency = args.get("currency_id", run.ctx.currency_id)
    currency = None if currency in (None, "") else int(currency)
    filters = api.search_filters(args)
    # The headline always says what is released within 30 days, so the schedule covers at least that much
    # even when a shorter window is asked for; the chart and the table keep to the window asked.
    span = max(days, 30)
    top, schedule = run.parallel(lambda: run.api.hold_top(entity, int(args.get("top_n") or 10), currency, filters),
                                 lambda: run.api.hold_schedule(span, currency, filters))
    cur = next((r.get("displayCurrency") for r in top + schedule if r.get("displayCurrency")), "")
    rows, total = split_total(top)
    merchants_total, banks_total = d(total, "merchantHold"), d(total, "bankHold")
    spec = report.new_spec(f"Money on hold, by {entity.lower()}", tool="ceo_holds",
                           period={"current": f"as of {run.ctx.today}"}, currency=cur)
    spec["kpis"] += [report.kpi("Held from merchants", merchants_total, "money"),
                     report.kpi("Held by banks", banks_total, "money")]
    for n in sorted({7, 30, days}):
        spec["kpis"].append(report.kpi(f"Released to merchants in {n} days",
                                       released_within(schedule, run.ctx.today, n, "merchantHold"), "money"))
    if merchants_total is not None:
        spec["summary"].append(f"We hold {fmt_money(merchants_total, cur)} of merchants' money; "
                               f"{fmt_money(released_within(schedule, run.ctx.today, 30, 'merchantHold'), cur)} "
                               "of it is released within 30 days.")
    limit = int((run.ctx.today + timedelta(days=days)).strftime("%Y%m%d"))
    dated = [r for r in schedule if r.get("releaseDay") not in (None, "") and int(r["releaseDay"]) < limit]
    if len(dated) > 1:
        spec["sections"].append({"title": "Release by day", "chart": {
            "type": "line", "unit": "money",
            "categories": [f"{str(r['releaseDay'])[4:6]}-{str(r['releaseDay'])[6:8]}" for r in dated],
            "series": [{"label": "to merchants", "values": [report.cell(d(r, "merchantHold")) for r in dated]},
                       {"label": "by banks", "values": [report.cell(d(r, "bankHold")) for r in dated],
                        "style": "dashed"}]}})
        upcoming = dated[:14]
        spec["sections"][-1]["table"] = report.table(
            [{"key": "day", "label": "Release day", "unit": "text"},
             {"key": "merchants", "label": "To merchants", "unit": "money"},
             {"key": "banks", "label": "By banks", "unit": "money"}],
            [{"day": f"{str(r['releaseDay'])[:4]}-{str(r['releaseDay'])[4:6]}-{str(r['releaseDay'])[6:8]}",
              "merchants": d(r, "merchantHold"), "banks": d(r, "bankHold")} for r in upcoming])
        if len(dated) > len(upcoming):
            spec["sections"][-1]["text"] = f"The first {len(upcoming)} release days of {len(dated)}; the chart has all."
    spec["sections"].append({"title": f"Largest holders by {entity.lower()}", "table": report.table(
        [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
         {"key": "merchants", "label": "Held from merchants", "unit": "money"},
         {"key": "share", "label": "Share", "unit": "rate"},
         {"key": "banks", "label": "Held by banks", "unit": "money"}],
        [{"entity": entity_label(r), "merchants": d(r, "merchantHold"),
          "share": share(d(r, "merchantHold"), merchants_total), "banks": d(r, "bankHold")} for r in rows])})
    spec["caveats"] += [
        "Held amounts by entity are the console's nightly snapshot; the release schedule reads the dated table "
        "and can differ from it within a day.",
        "Manager and provider are the current owners of the project and the gate, not as of the transaction."]
    return spec


# ---- ceo_entity -----------------------------------------------------------------------------------

def entity_card(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type")
    ident = api.int_list(args.get("entity_id"), "entity_id", 1)
    if not ident:
        raise InputError("entity_id is required")
    scoped = dict(args)
    scoped[FILTER_OF[entity]] = ident
    s = _scope(scoped, run.ctx)
    other = OTHER[entity]
    kpi, own, others, trend = run.parallel(
        lambda: run.api.kpi(s),
        lambda: run.api.top(s, "AMOUNT", "BOTH", entity, 1),
        lambda: run.api.top(s, "AMOUNT", "BOTH", other, 10),
        lambda: run.api.trend(s, "AMOUNT", "BOTH", entity))
    cur = _currency(kpi)
    own_rows, _ = split_total(own)
    me = own_rows[0] if own_rows else {}
    name = entity_label(me) if me else f"{entity.capitalize()} {ident[0]}"
    run.scope = s
    spec = report.new_spec(f"{entity.capitalize()} {name}", tool="ceo_entity", period=run.period(), currency=cur)
    for label, key, unit in BRIEF_TILES[:4]:
        delta = d(kpi, DELTA_KEY.get(key, key + "Delta"))
        spec["kpis"].append(report.kpi(label, d(kpi, key), unit, base=d(kpi, key + "Prev"), delta=delta,
                                       direction=direction(kpi.get(key + "Dir"))))
    for label, key in (("Manager margin", "flowManagerMargin"), ("Reseller margin", "flowResellerMargin")):
        if me:
            m, mb = d(me, key), d(me, key + "Prev")
            spec["kpis"].append(report.kpi(label, hidden(m), "money", base=hidden(mb), delta=sub(m, mb),
                                           direction=direction(sub(m, mb))))
    if not me:
        spec["summary"].append("No activity in the period or the base, or the entity is outside the token "
                               "owner's scope.")
    points = [p for p in trend if p.get("seriesKind") == "total"]
    cur_pts = sorted((p for p in points if p.get("periodFlag") == "current"), key=lambda p: p.get("bucketKey"))
    base_pts = sorted((p for p in points if p.get("periodFlag") != "current"), key=lambda p: p.get("bucketKey"))
    if len(cur_pts) > 1:
        def value(p):
            return (to_decimal(p.get("trendNumDeposit")) or Decimal(0)) + (to_decimal(p.get("trendNumWithdrawal"))
                                                                           or Decimal(0))
        cats = [str(p.get("bucketKey"))[4:] for p in cur_pts]
        base_vals = [report.cell(value(p)) for p in base_pts][:len(cats)]
        base_vals += [None] * (len(cats) - len(base_vals))
        spec["sections"].append({"title": "Approved amount by day, period against base (aligned by day number)",
                                 "chart": {"type": "line", "unit": "money", "categories": cats, "series": [
                                     {"label": "period", "values": [report.cell(value(p)) for p in cur_pts]},
                                     {"label": "base", "values": base_vals, "style": "dashed"}]}})
    other_rows, other_total = split_total(others)
    spec["sections"].append({"title": f"Through which {other.lower()}s", "table": report.table(
        [{"key": "entity", "label": other.capitalize(), "unit": "text"},
         {"key": "amount", "label": "Approved amount", "unit": "money"},
         {"key": "share", "label": "Share", "unit": "rate"},
         {"key": "rate", "label": "Approval rate", "unit": "rate"},
         {"key": "margin", "label": "Manager margin", "unit": "money"}],
        [{"entity": entity_label(r), "amount": d(r, "flowApprovedAmount"),
          "share": share(d(r, "flowApprovedAmount"), d(other_total, "flowApprovedAmount")),
          "rate": d(r, "flowApprovalRate"), "margin": hidden(d(r, "flowManagerMargin"))} for r in other_rows])})
    spec["caveats"] += KPI_CAVEATS + [MARGIN_CAVEAT] + run.standard_caveats()
    return spec


# ---- ceo_compare ----------------------------------------------------------------------------------

COMPARE_METRICS = ("AMOUNT", "CONVERSION", "APPROVAL", "DECLINE")


def compare(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type")
    ids = api.int_list(args.get("entity_ids"), "entity_ids", 5)
    if len(ids) < 2:
        raise InputError("entity_ids: give 2 to 5 entities to compare")
    metric = api.choice(args.get("metric"), COMPARE_METRICS, "metric", "AMOUNT")
    s = run.scope
    flt = dict(args)
    flt[FILTER_OF[entity]] = ids
    s_ids = _scope(flt, run.ctx)
    kpi, trend, rows = run.parallel(lambda: run.api.kpi(s),
                                    lambda: run.api.trend(s, metric, "BOTH", entity, ids, separate=True),
                                    lambda: run.api.top(s_ids, "AMOUNT", "BOTH", entity, len(ids)))
    cur = _currency(kpi)
    entities, _ = split_total(rows)
    spec = report.new_spec(f"{entity.capitalize()}s compared", tool="ceo_compare", period=run.period(), currency=cur)
    rate_metric = metric in ("CONVERSION", "DECLINE")

    def bucket_value(p):
        nums = [to_decimal(p.get("trendNumDeposit")), to_decimal(p.get("trendNumWithdrawal"))]
        dens = [to_decimal(p.get("trendDenDeposit")), to_decimal(p.get("trendDenWithdrawal"))]
        num = sum((n for n in nums if n is not None), Decimal(0))
        if not rate_metric:
            return num
        return ratio(num, sum((x for x in dens if x is not None), Decimal(0)))

    series: Dict[Any, List[Dict[str, Any]]] = {}
    for p in trend:
        if p.get("seriesKind") == "entity" and p.get("periodFlag") == "current":
            series.setdefault(p.get("seriesId"), []).append(p)
    buckets = sorted({p.get("bucketKey") for pts in series.values() for p in pts})
    chart_series, worst = [], {}
    for sid, pts in series.items():
        by_bucket = {p.get("bucketKey"): bucket_value(p) for p in pts}
        values = [by_bucket.get(b) for b in buckets]
        chart_series.append({"label": str(pts[0].get("seriesName") or sid),
                             "values": [report.cell(v) if v is not None else None for v in values]})
        known = [(v, b) for v, b in zip(values, buckets) if v is not None]
        if known:
            worst[sid] = (max if metric == "DECLINE" else min)(known)[1]
    if len(buckets) > 1:
        spec["sections"].append({"title": f"{metric.lower().replace('_', ' ')} by day",
                                 "chart": {"type": "line", "unit": "rate" if rate_metric else
                                           ("count" if metric == "APPROVAL" else "money"),
                                           "categories": [str(b)[4:] for b in buckets], "series": chart_series}})
    table = []
    for r in entities:
        a, ab = d(r, "flowApprovedAmount"), d(r, "flowApprovedAmountPrev")
        rt, rtb = d(r, "flowApprovalRate"), d(r, "flowApprovalRatePrev")
        wb = worst.get(r.get("entityId"))
        table.append({"entity": entity_label(r), "amount": a, "amount_change": rel_change(a, ab), "rate": rt,
                      "rate_change": sub(rt, rtb), "worst": f"{str(wb)[:4]}-{str(wb)[4:6]}-{str(wb)[6:8]}" if wb else "—"})
    spec["sections"].append({"title": "Side by side", "table": report.table(
        [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
         {"key": "amount", "label": "Approved amount", "unit": "money"},
         {"key": "amount_change", "label": "Change to base", "unit": "rate"},
         {"key": "rate", "label": "Approval rate", "unit": "rate"},
         {"key": "rate_change", "label": "Change", "unit": "pp"},
         {"key": "worst", "label": "Weakest day", "unit": "text"}], table)})
    falling = [t["entity"] for t in table if t["amount_change"] is not None and t["amount_change"] < 0]
    if falling and len(falling) == len(table):
        spec["summary"].append("All of them fell against the base: look for a common cause (provider, rules, season).")
    elif falling:
        spec["summary"].append("Fell against the base: " + ", ".join(falling) + "; the others did not.")
    spec["caveats"] += run.standard_caveats() + ["«Change to base» of the approved amount is (now − base) / base."]
    return spec


# ---- ceo_split ------------------------------------------------------------------------------------

def split(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    entity = api.choice(args.get("entity_type"), ("MERCHANT", "PROVIDER"), "entity_type", "MERCHANT")
    ids = api.int_list(args.get("entity_ids"), "entity_ids", 6)
    if not ids:
        raise InputError("entity_ids: give 1 to 6 entities")
    other = OTHER[entity]
    s = run.scope
    names_args = dict(args)
    names_args[FILTER_OF[entity]] = ids
    s_names = _scope(names_args, run.ctx)
    jobs = [lambda: run.api.kpi(s), lambda: run.api.top(s_names, "AMOUNT", "BOTH", entity, len(ids))]
    scopes = []
    for i in ids:
        one = dict(args)
        one[FILTER_OF[entity]] = [i]
        scopes.append(_scope(one, run.ctx))
    jobs += [(lambda sc=sc: run.api.top(sc, "AMOUNT", "BOTH", other, None)) for sc in scopes]
    results = run.parallel(*jobs)
    kpi, names = results[0], results[1]
    cur = _currency(kpi)
    label_of = {r.get("entityId"): entity_label(r) for r in split_total(names)[0]}
    spec = report.new_spec(f"{entity.capitalize()} × {other.lower()}", tool="ceo_split", period=run.period(),
                           currency=cur)
    rows = []
    for i, result in zip(ids, results[2:]):
        entities, total = split_total(result)
        for r in entities:
            rows.append({"entity": label_of.get(i, f"{entity.capitalize()} {i}"), "other": entity_label(r),
                         "amount": d(r, "flowApprovedAmount"),
                         "share": share(d(r, "flowApprovedAmount"), d(total, "flowApprovedAmount")),
                         "rate": d(r, "flowApprovalRate"), "margin": hidden(d(r, "flowManagerMargin"))})
    spec["sections"].append({"title": f"Approved amount and margin by {other.lower()}", "table": report.table(
        [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
         {"key": "other", "label": other.capitalize(), "unit": "text"},
         {"key": "amount", "label": "Approved amount", "unit": "money"},
         {"key": "share", "label": f"Share of the {entity.lower()}", "unit": "rate"},
         {"key": "rate", "label": "Approval rate", "unit": "rate"},
         {"key": "margin", "label": "Manager margin", "unit": "money"}], rows)})
    spec["caveats"] += [MARGIN_CAVEAT] + run.standard_caveats()
    return spec


# ---- ceo_risk -------------------------------------------------------------------------------------

def _count(tiles: List[Dict[str, Any]], kind: str) -> Optional[Decimal]:
    tile = next((t for t in tiles if t.get("type") == kind), None)
    return to_decimal(tile.get("approvedCountFormatted")) if tile else None


def _by_entity(items: List[Dict[str, Any]]) -> Dict[Any, Tuple[str, Decimal]]:
    """Counts per entity, summed over the per-currency rows the monitor returns."""
    out: Dict[Any, Tuple[str, Decimal]] = {}
    for it in items:
        ident = next((v for k, v in it.items() if k.endswith("Id")), None)
        name = next((v for k, v in it.items() if k.endswith("Name")), "")
        count = to_decimal(it.get("approvedCountFormatted")) or Decimal(0)
        prev = out.get(ident, (name, Decimal(0)))
        out[ident] = (name, prev[1] + count)
    return out


def risk(run: Run, args: Dict[str, Any]) -> Dict[str, Any]:
    s = run.scope
    entity = api.choice(args.get("entity_type"), api.ENTITIES, "entity_type", "MERCHANT")
    try:
        threshold = to_decimal(args.get("threshold")) if args.get("threshold") not in (None, "") else Decimal("0.01")
    except ValueError:
        raise InputError("threshold must be a fraction, e.g. 0.01") from None
    base = s.effective_base
    now_tiles, base_tiles, cb, sales, fraud = run.parallel(
        lambda: run.api.monitor_total(s.current, (), (), s.filters),
        lambda: run.api.monitor_total(base, (), (), s.filters),
        lambda: run.api.monitor_by_entity(entity, "CHARGEBACK", s.current, filters=s.filters),
        lambda: run.api.monitor_by_entity(entity, "SALE", s.current, filters=s.filters),
        lambda: run.api.monitor_by_entity(entity, "FRAUD", s.current, filters=s.filters))
    spec = report.new_spec(f"Chargebacks, fraud and reversals by {entity.lower()}", tool="ceo_risk",
                           period=run.period())
    for kind in ("CHARGEBACK", "FRAUD", "REVERSAL", "CHARGEBACK_REVERSAL", "ARBITRATION"):
        now_c, base_c = _count(now_tiles, kind), _count(base_tiles, kind)
        spec["kpis"].append(report.kpi(kind.replace("_", " ").capitalize(), now_c, "count", base=base_c,
                                       delta=sub(now_c, base_c),
                                       direction=None if sub(now_c, base_c) is None else -direction(sub(now_c, base_c))))
    cbr_now = ratio(_count(now_tiles, "CHARGEBACK"), _count(now_tiles, "SALE"))
    cbr_base = ratio(_count(base_tiles, "CHARGEBACK"), _count(base_tiles, "SALE"))
    spec["kpis"].append(report.kpi("Chargeback ratio (chargebacks / approved sales)", cbr_now, "rate", base=cbr_base,
                                   delta=sub(cbr_now, cbr_base)))
    cb_by, sales_by, fraud_by = _by_entity(cb), _by_entity(sales), _by_entity(fraud)
    rows = []
    for ident in set(cb_by) | set(fraud_by):
        name = (cb_by.get(ident) or fraud_by.get(ident) or sales_by.get(ident) or ("", Decimal(0)))[0]
        n_cb = cb_by.get(ident, ("", Decimal(0)))[1]
        n_sales = sales_by.get(ident, ("", Decimal(0)))[1]
        r = ratio(n_cb, n_sales)
        rows.append({"entity": f"{ident} · {name}", "sales": n_sales, "chargebacks": n_cb, "ratio": r,
                     "fraud": fraud_by.get(ident, ("", Decimal(0)))[1],
                     "flag": "above threshold" if r is not None and r >= threshold else ""})
    rows.sort(key=lambda x: -(x["ratio"] or Decimal(0)))
    flagged = [x["entity"] for x in rows if x["flag"]]
    spec["summary"].append(f"Chargeback ratio {fmt_rate(cbr_now)} (base {fmt_rate(cbr_base)}, "
                           f"{fmt_pp(sub(cbr_now, cbr_base))}); "
                           f"{len(flagged)} {entity.lower()}(s) at or above {fmt_rate(threshold)}.")
    spec["sections"].append({"title": f"{entity.capitalize()}s with chargebacks or fraud", "table": report.table(
        [{"key": "entity", "label": entity.capitalize(), "unit": "text"},
         {"key": "sales", "label": "Approved sales", "unit": "count"},
         {"key": "chargebacks", "label": "Chargebacks", "unit": "count"},
         {"key": "ratio", "label": "Chargeback ratio", "unit": "rate"},
         {"key": "fraud", "label": "Fraud reports", "unit": "count"},
         {"key": "flag", "label": "", "unit": "text"}], rows[:50])})
    spec["caveats"] += [
        "Counted by status-change date, in counts: a chargeback is counted in the period it arrived, "
        "not in the period of the sale it disputes.",
        f"The threshold {fmt_rate(threshold)} is a parameter of this tool, not a payment-network rule.",
    ] + run.standard_caveats()
    return spec


# ---- the tools ------------------------------------------------------------------------------------

def build(context: Context) -> List[Tool]:
    def tool(name: str, title: str, description: str, fn, props: Dict[str, Any], required=None,
             scoped: bool = True, max_calls: int = 8) -> Tool:
        def handler(a: Dict[str, Any]) -> ToolResult:
            ctx = context(a.get("profile"))
            run = Run(ctx, _scope(a, ctx) if scoped else None, max_calls)
            return finish(run, fn(run, a), a)
        return Tool(name, description, schema(business_props(**props), required), handler, title)

    entity = {"entity_type": ENTITY}
    return [
        tool("ceo_briefing", "Business briefing",
             "How is the business doing, what moved the bad way and who caused it: headline KPIs against the base, "
             "the merchants that added and lost the most, providers with the highest decline rate, chargebacks, "
             "fraud and reversals.", briefing, {}),
        tool("ceo_earnings", "Earnings",
             "How much did we earn and on whom: the margin of a tier (manager, reseller or both) against the base, "
             "the top entities by it with their share and effective rate, and large-volume entities that earn below "
             "the average rate. Needs the tariff rights of the tier.", earnings,
             {**entity, "tier": {"type": "string", "enum": list(TIERS)}, "flow": FLOW,
              "top_n": {"type": "integer", "minimum": 1, "maximum": 50}}),
        tool("ceo_movers", "Who grew and who fell",
             "Which entities grew, fell, appeared or went quiet against the base, by approved amount, approved "
             "count or a margin.", movers_tool,
             {**entity, "metric": {"type": "string", "enum": list(MOVER_METRICS)}, "flow": FLOW,
              "n": {"type": "integer", "minimum": 1, "maximum": 50},
              "min_change": {"type": "number", "description": "ignore changes smaller than this"}}),
        tool("ceo_concentration", "Concentration",
             "How dependent the business is on its largest merchants, providers or managers: shares of the top "
             "1, 5 and 10 and HHI, against the base.", concentration_tool,
             {**entity, "metric": {"type": "string", "enum": list(CONC_METRICS)}}),
        tool("ceo_holds", "Money on hold",
             "How much money is on hold now — held from merchants and held by banks — who holds the most, and "
             "how much is released in 7, 30 and N days. Needs the manager tariff right.", holds,
             {**entity, "days": {"type": "integer", "minimum": 1, "maximum": 366},
              "top_n": {"type": "integer", "minimum": 1, "maximum": 100}}, scoped=False),
        tool("ceo_entity", "One entity",
             "How one merchant, provider or manager is doing: KPIs against the base, margins, approved amount by "
             "day, and through which providers (or merchants) its volume goes.", entity_card,
             {**entity, "entity_id": {"type": "integer"}}, ["entity_type", "entity_id"]),
        tool("ceo_compare", "Compare entities",
             "Compare 2–5 merchants, providers or managers by day and side by side: approved amount and approval "
             "rate against the base, and each one's weakest day.", compare,
             {**entity, "entity_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 5},
              "metric": {"type": "string", "enum": list(COMPARE_METRICS)}}, ["entity_type", "entity_ids"]),
        tool("ceo_split", "Merchant × provider",
             "Through which providers each of up to 6 merchants processes (or which merchants each provider "
             "carries), with approved amount, share, approval rate and margin.", split,
             {"entity_type": {"type": "string", "enum": ["MERCHANT", "PROVIDER"]},
              "entity_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 6}},
             ["entity_ids"]),
        tool("ceo_risk", "Chargebacks and fraud",
             "Chargebacks, fraud reports and reversals against the base, the chargeback ratio, and the entities at "
             "or above a ratio threshold.", risk,
             {**entity, "threshold": {"type": "number", "description": "chargeback ratio to flag, default 0.01"}}),
    ]
