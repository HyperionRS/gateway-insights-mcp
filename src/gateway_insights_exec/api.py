# SPDX-License-Identifier: Apache-2.0
"""The raw layer of the executive server: one function per console endpoint.

Business tools call these functions and nothing else, so every number they
report is a number a raw call returns. Each function validates its arguments,
builds the request body and returns the console's answer with numbers parsed
to Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from gateway_insights_core import ids
from gateway_insights_core.dates import Window, base_window, implied_base, window
from gateway_insights_core.errors import InputError
from gateway_insights_core import entities
from gateway_insights_core.http import ConsoleClient

KPI = "/exec-dashboard/kpi"
TREND = "/exec-dashboard/trend"
TOP = "/exec-dashboard/top"
HOLD_TOP = "/exec-dashboard/hold-top"
HOLD_SCHEDULE = "/exec-dashboard/hold-schedule"
MONITOR_TOTAL = "/charts/transactionMonitorTotal"
MONITOR_MERCHANTS = "/charts/transactionMonitorMerchants"
MONITOR_MANAGERS = "/charts/transactionMonitorManagers"
MONITOR_PROVIDERS = "/charts/transactionMonitorProviders"

ALLOWED_PATHS = frozenset({KPI, TREND, TOP, HOLD_TOP, HOLD_SCHEDULE, MONITOR_TOTAL,
                           MONITOR_MERCHANTS, MONITOR_MANAGERS, MONITOR_PROVIDERS}) | entities.PATHS

GRAINS = ("DAY", "HOUR")
FLOWS = ("BOTH", "DEPOSIT", "WITHDRAWAL")
ENTITIES = ("MERCHANT", "PROVIDER", "MANAGER")
TREND_METRICS = ("TOTAL_COUNT", "APPROVAL", "DECLINE", "AMOUNT", "FILTERED", "CONVERSION", "AVG_TICKET")
TOP_METRICS = ("APPROVAL", "DECLINE", "AMOUNT", "CONVERSION", "AVG_TICKET", "TOTAL",
               "MANAGER_MARGIN", "RESELLER_MARGIN", "TOTAL_MARGIN")
MONITOR_TYPES = ("SALE", "REVERSAL", "PREAUTH", "CAPTURE", "CANCEL", "FRAUD", "CHARGEBACK", "RETRIEVAL",
                 "PAYOUT", "TRANSFER", "SCORING", "VOID", "ACCOUNT_VERIFICATION", "CHARGEBACK_REVERSAL",
                 "PREARBITRATION", "ARBITRATION", "PAYOUT_CANCEL")

FILTERS = ids.ENTITY_FILTERS
MAX_IDS = 50
MAX_SELECTED = 20
choice = ids.choice


def int_list(values: Any, what: str, limit: int) -> List[int]:
    return ids.int_list(values, what, limit)


def search_filters(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ids.search_filters(args, MAX_IDS)


@dataclass(frozen=True)
class Scope:
    """The common part of every exec request: window, base, grain, currency, filters."""
    current: Window
    base: Optional[Window]          # explicit base, or None for «right before»
    grain: str
    currency_id: Optional[int]
    filters: List[Dict[str, Any]]

    @property
    def effective_base(self) -> Optional[Window]:
        if self.grain == "HOUR":
            return None
        return self.base or implied_base(self.current)

    def body(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"bankDate": self.current.as_range(), "grain": self.grain}
        if self.currency_id is not None:
            out["toCurrencyId"] = self.currency_id
        if self.base is not None:
            out["baseBankDate"] = self.base.as_range()
        if self.filters:
            out["searchFilters"] = self.filters
        return out


def scope(args: Dict[str, Any], today: date, default_currency: Optional[int]) -> Scope:
    grain = choice(args.get("grain"), GRAINS, "grain", "DAY")
    current = window(args.get("date_from"), args.get("date_to"), today, default_days=7)
    base = base_window(args.get("base_from"), args.get("base_to"), current)
    if grain == "HOUR" and base is not None:
        raise InputError("an explicit base works with the DAY grain only; HOUR is the last 24 hours "
                         "against the 24 hours before")
    currency = args.get("currency_id", default_currency)
    try:
        currency_id = None if currency in (None, "") else int(currency)
    except (TypeError, ValueError):
        raise InputError(f"currency_id: {currency!r} is not an integer") from None
    return Scope(current, base, grain, currency_id, search_filters(args))


class ExecApi:
    def __init__(self, client: ConsoleClient) -> None:
        self.client = client

    def kpi(self, s: Scope) -> Dict[str, Any]:
        return self.client.post(KPI, s.body(), what="executive KPI")

    def trend(self, s: Scope, metric: str, flow: str, entity: str,
              selected_ids: Sequence[int] = (), separate: bool = False) -> List[Dict[str, Any]]:
        body = s.body()
        body.update({"metricMode": choice(metric, TREND_METRICS, "metric"),
                     "flowMode": choice(flow, FLOWS, "flow"),
                     "entityType": choice(entity, ENTITIES, "entity_type")})
        ids = int_list(list(selected_ids), "entity_ids", MAX_SELECTED)
        if ids:
            body["selectedEntityIds"] = ids
            body["selectedSplit"] = "SEPARATE" if separate else "COMBINED"
        return list(self.client.post(TREND, body, what="executive trend").get("rows") or [])

    def top(self, s: Scope, metric: str, flow: str, entity: str,
            top_n: Optional[int] = 10, demo_names: bool = False) -> List[Dict[str, Any]]:
        body = s.body()
        body.update({"metricMode": choice(metric, TOP_METRICS, "metric"),
                     "flowMode": choice(flow, FLOWS, "flow"),
                     "entityType": choice(entity, ENTITIES, "entity_type"),
                     "nameType": "DEMO" if demo_names else "REAL"})
        if top_n is not None:
            if not 1 <= int(top_n) <= 100:
                raise InputError("top_n: 1..100, or null for every entity")
            body["topN"] = int(top_n)
        return list(self.client.post(TOP, body, what="executive top").get("rows") or [])

    def hold_top(self, entity: str, top_n: Optional[int], currency_id: Optional[int],
                 filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"entityType": choice(entity, ENTITIES, "entity_type"), "nameType": "REAL"}
        if top_n is not None:
            if not 1 <= int(top_n) <= 100:
                raise InputError("top_n: 1..100, or null for every entity")
            body["topN"] = int(top_n)
        if currency_id is not None:
            body["toCurrencyId"] = currency_id
        if filters:
            body["searchFilters"] = filters
        return list(self.client.post(HOLD_TOP, body, what="money on hold").get("rows") or [])

    def hold_schedule(self, days: int, currency_id: Optional[int],
                      filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not 1 <= int(days) <= 366:
            raise InputError("days: 1..366")
        body: Dict[str, Any] = {"days": int(days)}
        if currency_id is not None:
            body["toCurrencyId"] = currency_id
        if filters:
            body["searchFilters"] = filters
        return list(self.client.post(HOLD_SCHEDULE, body, what="hold release schedule").get("rows") or [])

    def _monitor_body(self, period: Window, currency_ids: Sequence[int], card_type_ids: Sequence[int],
                      filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        body: Dict[str, Any] = {"statusChanged": period.as_range()}
        if currency_ids:
            body["currIdList"] = ",".join(str(i) for i in currency_ids)
        if card_type_ids:
            body["crtpIdList"] = ",".join(str(i) for i in card_type_ids)
        if filters:
            body["searchFilters"] = filters
        return body

    def monitor_total(self, period: Window, currency_ids: Sequence[int] = (), card_type_ids: Sequence[int] = (),
                      filters: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        body = self._monitor_body(period, currency_ids, card_type_ids, filters or [])
        return list(self.client.post(MONITOR_TOTAL, body, what="transaction monitor").get("tiles") or [])

    def monitor_by_entity(self, entity: str, operation: str, period: Window, currency_ids: Sequence[int] = (),
                          card_type_ids: Sequence[int] = (),
                          filters: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        path = {"MERCHANT": MONITOR_MERCHANTS, "MANAGER": MONITOR_MANAGERS,
                "PROVIDER": MONITOR_PROVIDERS}[choice(entity, ENTITIES, "entity_type")]
        body = self._monitor_body(period, currency_ids, card_type_ids, filters or [])
        body["transactionType"] = choice(operation, MONITOR_TYPES, "operation")
        return list(self.client.post(path, body, what="transaction monitor by entity").get("items") or [])
