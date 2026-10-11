# SPDX-License-Identifier: Apache-2.0
"""What the executive business tools share on top of the core frame: the scope, the period line,
the standard caveats and small value helpers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from gateway_insights_core import report
from gateway_insights_core.business import REPORT_PROP, CallRun, finish  # noqa: F401  (re-exported)
from gateway_insights_core.numbers import to_decimal

from . import api
from .api import Scope
from .raw_tools import FILTER_PROFILE_PROPS, ToolContext

RAW_TOOL_OF_PATH = {
    api.KPI: "exec_kpi", api.TREND: "exec_trend", api.TOP: "exec_top", api.HOLD_TOP: "exec_hold_top",
    api.HOLD_SCHEDULE: "exec_hold_schedule", api.MONITOR_TOTAL: "monitor_total",
    api.MONITOR_MERCHANTS: "monitor_by_entity", api.MONITOR_MANAGERS: "monitor_by_entity",
    api.MONITOR_PROVIDERS: "monitor_by_entity",
}

KPI_CAVEATS = [
    "Average ticket = approved amount / all transactions, declines and filtered included.",
    "Approval rate counts filtered transactions in the denominator.",
    "Amounts are in the display currency at the latest rate for both periods; not for reconciliation.",
]
MARGIN_CAVEAT = "Earnings are summed over all statuses."
LENGTH_CAVEAT = "The base is not as long as the period: rates compare, counts and amounts do not."
TODAY_CAVEAT = "The period includes today, which is not over yet."
FILTER_CAVEAT = ("An entity filter the token owner has no list right for is dropped by the console; "
                 "the result is then wider than asked.")


class Run(CallRun):
    """One executive business tool call: its context, scope and budget."""

    def __init__(self, ctx: ToolContext, scope: Optional[Scope] = None, max_calls: int = 8) -> None:
        super().__init__(ctx.api.client, RAW_TOOL_OF_PATH, max_calls)
        self.ctx = ctx
        self.scope = scope

    @property
    def api(self) -> api.ExecApi:
        return self.ctx.api

    def period(self) -> Dict[str, str]:
        if self.scope is None:
            return {}
        base = self.scope.effective_base
        return {"current": str(self.scope.current), "base": str(base) if base else ""}

    def standard_caveats(self) -> List[str]:
        out = []
        if self.scope is not None:
            base = self.scope.effective_base
            if base is not None and base.days != self.scope.current.days:
                out.append(LENGTH_CAVEAT)
            if self.scope.current.end >= self.ctx.today:
                out.append(TODAY_CAVEAT)
            if self.scope.filters:
                out.append(FILTER_CAVEAT)
        return out


def business_props(**extra: Any) -> Dict[str, Any]:
    out = dict(FILTER_PROFILE_PROPS)
    out.update(REPORT_PROP)
    out.update(extra)
    return out


def d(row: Dict[str, Any], key: str) -> Optional[Decimal]:
    return to_decimal(row.get(key))


def sub(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[Decimal]:
    return None if a is None or b is None else a - b


def rel_change(now: Optional[Decimal], base: Optional[Decimal]) -> Optional[Decimal]:
    """(now − base) / |base|; None when the base is absent or zero. The absolute value keeps the sign
    meaning «up» when the base is negative (a margin going from −782 to −760 rose)."""
    if now is None or base is None or base == 0:
        return None
    return (now - base) / abs(base)


def hidden(value: Optional[Decimal]) -> Any:
    """A tariff field the console returned as null: shown as «no access» in the spec."""
    return report.HIDDEN if value is None else value


def entity_label(row: Dict[str, Any]) -> str:
    if to_decimal(row.get("isGrandTotal")):
        return "Total"
    ident, name = row.get("entityId"), row.get("entityName") or ""
    return f"{ident} · {name}" if ident is not None else name


def split_total(rows: Sequence[Dict[str, Any]]):
    """(entity rows, grand total row or {})."""
    entities = [r for r in rows if not to_decimal(r.get("isGrandTotal"))]
    total = next((r for r in rows if to_decimal(r.get("isGrandTotal"))), {})
    return entities, total


def direction(value: Any) -> Optional[int]:
    n = to_decimal(value)
    return None if n is None else (1 if n > 0 else -1 if n < 0 else 0)
