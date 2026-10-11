# SPDX-License-Identifier: Apache-2.0
"""Arguments every server takes the same way: a choice from a fixed list, a list of ids, entity filters.

One implementation, so a rule (ids are whole and not negative, a limit per list) holds everywhere.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .errors import InputError

# Entity filter arguments -> the console's entity type. Inside a type the ids are OR-ed, between types
# AND-ed. A type the token owner may not list is dropped by the console, which widens the result.
ENTITY_FILTERS = {
    "merchant_ids": "MERCHANT", "provider_ids": "PROVIDER", "manager_ids": "MANAGER",
    "bank_terminal_ids": "BANK_TERMINAL", "requestor_ids": "REQUESTOR", "blueprint_ids": "BLUEPRINT",
    "channel_ids": "CHANNEL", "reseller_ids": "RESELLER",
}


def choice(value: Any, allowed: Sequence[str], what: str, default: Optional[str] = None) -> str:
    text = str(value or default or "").strip().upper()
    if text not in allowed:
        raise InputError(f"{what}: {value!r} is not one of {', '.join(allowed)}")
    return text


def int_list(values: Any, what: str, limit: int) -> List[int]:
    """Whole, non-negative, without repeats, at most `limit`."""
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    out: List[int] = []
    for v in values:
        if isinstance(v, bool):
            raise InputError(f"{what}: {v!r} is not an integer id")
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise InputError(f"{what}: {v!r} is not an integer id") from None
        if n < 0:
            raise InputError(f"{what}: ids are not negative")
        if n not in out:
            out.append(n)
    if len(out) > limit:
        raise InputError(f"{what}: at most {limit} ids")
    return out


def search_filters(args: Dict[str, Any], limit: int, only: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """The console's searchFilters from *_ids arguments; `only` narrows the arguments a caller accepts."""
    out = []
    for param, entity in ENTITY_FILTERS.items():
        if only is not None and param not in only:
            continue
        ids = int_list(args.get(param), param, limit)
        if ids:
            out.append({"entityType": entity, "items": [{"id": i, "name": ""} for i in ids]})
    return out
