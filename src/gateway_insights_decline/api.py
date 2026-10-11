# SPDX-License-Identifier: Apache-2.0
"""The raw layer of the decline server: one function per Decline Dashboard endpoint.

The dashboard counts card operations by bank date, whole days, and splits declines by card
characteristics (country, issuing bank, funding source, card type, product) and by entity
(requestor, blueprint, merchant, manager, bank terminal, provider). Card criteria narrow every
breakdown except the breakdown of the criterion's own dimension — the console's linked-filter rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List

from gateway_insights_core.dates import Window, base_window, implied_base, window
from gateway_insights_core.errors import InputError
from gateway_insights_core import entities, ids, restrictions, routing
from gateway_insights_core.http import ConsoleClient

KPI = "/decline-dashboard/kpi"
HEATMAP = "/decline-dashboard/heatmap"
COMPARE = "/decline-dashboard/compare"
ALLOWED_PATHS = frozenset({KPI, HEATMAP, COMPARE}) | entities.PATHS | routing.PATHS | restrictions.PATHS

MAX_DAYS = 366
CARD_DIMENSIONS = ("CARD_COUNTRY", "BANK", "FUNDING_SOURCE", "CARD_TYPE", "PRODUCT_CODE")
ENTITY_DIMENSIONS = ("REQUESTOR", "BLUEPRINT", "MERCHANT", "MANAGER", "BANK_TERMINAL", "PROVIDER")
DIMENSIONS = CARD_DIMENSIONS + ENTITY_DIMENSIONS
DIRECTIONS = ("SRC", "DST")
HEATMAP_SORTS = ("DECLINED", "RATE", "TOTAL")
COMPARE_SORTS = ("EXCESS", "DELTA", "Z", "TOTAL")

# Card criteria: tool argument -> request field. Id 0 is the console's «unknown».
CRITERIA = {"card_country_ids": "cardCountries", "bank_ids": "banks", "funding_source_ids": "fundingSources",
            "card_type_ids": "cardTypes", "product_ids": "products"}
CRITERION_DIMENSION = {"card_country_ids": "CARD_COUNTRY", "bank_ids": "BANK", "funding_source_ids": "FUNDING_SOURCE",
                       "card_type_ids": "CARD_TYPE", "product_ids": "PRODUCT_CODE"}
FILTERS = ids.ENTITY_FILTERS
# Entity dimension -> the entity filter that narrows to one of its rows.
FILTER_OF_DIMENSION = {"MERCHANT": "merchant_ids", "PROVIDER": "provider_ids", "MANAGER": "manager_ids",
                       "BANK_TERMINAL": "bank_terminal_ids", "REQUESTOR": "requestor_ids", "BLUEPRINT": "blueprint_ids"}
MAX_IDS = 50


choice = ids.choice


def int_list(values: Any, what: str, limit: int = MAX_IDS) -> List[int]:
    return ids.int_list(values, what, limit)


@dataclass(frozen=True)
class Scope:
    current: Window
    base: Window                      # always set: explicit, or the same length right before
    base_explicit: bool
    direction: str
    criteria: Dict[str, List[Dict[str, Any]]]
    filters: List[Dict[str, Any]]

    def common(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"statusChanged": self.current.as_range(), "direction": self.direction}
        if self.criteria:
            out["criteria"] = self.criteria
        if self.filters:
            out["searchFilters"] = self.filters
        return out


def scope(args: Dict[str, Any], today: date) -> Scope:
    current = window(args.get("date_from"), args.get("date_to"), today, default_days=7, max_days=MAX_DAYS)
    explicit = base_window(args.get("base_from"), args.get("base_to"), current, max_days=MAX_DAYS)
    criteria = {}
    for param, field in CRITERIA.items():
        ids = int_list(args.get(param), param)
        if ids:
            criteria[field] = [{"id": i, "name": ""} for i in ids]
    return Scope(current, explicit or implied_base(current), explicit is not None,
                 choice(args.get("direction"), DIRECTIONS, "direction", "SRC"), criteria, search_filters(args))


def search_filters(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ids.search_filters(args, MAX_IDS)


class DeclineApi:
    def __init__(self, client: ConsoleClient) -> None:
        self.client = client

    def kpi(self, s: Scope, current: bool = True) -> Dict[str, Any]:
        body = s.common()
        if not current:
            body["statusChanged"] = s.base.as_range()
        return self.client.post(KPI, body, what="decline KPI")

    def heatmap(self, s: Scope, dimension: str, top_n: int = 10, sort: str = "DECLINED") -> List[Dict[str, Any]]:
        if not 1 <= int(top_n) <= 100:
            raise InputError("top_n: 1..100")
        body = s.common()
        body.update({"dimension": choice(dimension, DIMENSIONS, "dimension"), "topN": int(top_n),
                     "sort": choice(sort, HEATMAP_SORTS, "sort")})
        return list(self.client.post(HEATMAP, body, what="decline breakdown").get("rows") or [])

    def compare(self, s: Scope, dimension: str, top_n: int = 10, sort: str = "EXCESS") -> Dict[str, Any]:
        if not 1 <= int(top_n) <= 100:
            raise InputError("top_n: 1..100")
        body = s.common()
        body.update({"dimension": choice(dimension, DIMENSIONS, "dimension"), "topN": int(top_n),
                     "sort": choice(sort, COMPARE_SORTS, "sort"), "baseStatusChanged": s.base.as_range()})
        data = self.client.post(COMPARE, body, what="decline comparison")
        return {"summary": data.get("summary"), "rows": list(data.get("rows") or [])}
