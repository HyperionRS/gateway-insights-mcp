# SPDX-License-Identifier: Apache-2.0
"""Which batch tasks the model may run, and which only a person runs in the console.

  class 1 — reversible: entity edits with a known way back. The server runs them.
  class 2 — money and money paths: order operations, putting a terminal into a route, replacing one.
            The server prepares them; a person runs them in the console.
  class 3 — secrets: anything that sets a credential. Prepared with the value taken by reference;
            a person runs them in the console.

A task's class is the highest of what it actually does: a type of class 1 rises to 2 when the task
switches routing rows on, and to 3 when it carries a credential. A type this table does not know is
class 2 until someone classifies it — the server stays complete, and new types stay safe.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, Iterable, List, Optional

REVERSIBLE, MONEY, SECRET = 1, 2, 3
NAMES = {REVERSIBLE: "reversible", MONEY: "money or money path", SECRET: "secret"}

TYPE_CLASS: Dict[str, int] = {
    # orders
    "ORDER_QUERY_STATUS": REVERSIBLE,
    **{t: MONEY for t in (
        "ORDER_RESOLVE_UNKNOWN", "ORDER_CHARGEBACK", "ORDER_FRAUD", "ORDER_RETRIEVAL", "ORDER_REVERSAL",
        "ORDER_FAUX_REVERSAL", "ORDER_CAPTURE", "ORDER_VOID", "ORDER_CANCEL", "ORDER_PAYOUT_CANCEL",
        "ORDER_CHARGEBACK_REVERSAL", "ORDER_PREARBITRATION", "ORDER_ARBITRATION", "ORDER_APPROVE",
        "ORDER_DECLINE", "ORDER_ROLLBACK_AND_CHECK", "ORDER_CHANGE_PROVIDER_ORDER_ID", "ORDER_CHANGE_DATE",
        "ORDER_ROLLBACK_TO_STATE")},
    # bank terminals and routing
    "BANK_TERMINAL_PROPERTY": REVERSIBLE,          # 3 with a credential
    "BANK_TERMINAL_PROPERTY_CSV": REVERSIBLE,      # 3 with a credential
    "BANK_TERMINAL_PSP_SECRET": SECRET,
    "BANK_TERMINAL_ROUTING_STATUS": REVERSIBLE,    # 2 when it switches rows on
    "BANK_TERMINAL_ROUTING_REPLACE": MONEY,
    "BANK_TERMINAL_CLONE_EXTEND": REVERSIBLE,      # 2 with rows enabled, 3 with credentials
    "BANK_TERMINAL_CLONE_REPLACE": MONEY,          # 3 with credentials
    # No batch type removes a criterion value, so an added one has no way back by batch.
    "ROUTING_CRITERION_ADD_VALUE": MONEY,
    "ROUTING_CRITERION_REPLACE_VALUE": REVERSIBLE,
    # requestors, merchants, any level
    "RESTRICTION_PARAM": REVERSIBLE,
    "ENTITY_NAME": REVERSIBLE,
    "ENTITY_RATE_PLAN": REVERSIBLE,
    "ENTITY_ENABLE": REVERSIBLE,
    "REQUESTOR_CONFIG": REVERSIBLE,
    "REQUESTOR_EAV": REVERSIBLE,
    "REQUESTOR_API_HOST": REVERSIBLE,
    "REQUESTOR_ATTRIBUTE": REVERSIBLE,
    "MERCHANT_GENERATOR": REVERSIBLE,
}
# Types whose class depends on what the task carries.
ROUTING_ON = {"BANK_TERMINAL_ROUTING_STATUS": "row_status", "BANK_TERMINAL_CLONE_EXTEND": "enabled",
              "BANK_TERMINAL_CLONE_REPLACE": "enabled"}
CREDENTIALS = {"BANK_TERMINAL_PROPERTY", "BANK_TERMINAL_PROPERTY_CSV", "BANK_TERMINAL_CLONE_EXTEND",
               "BANK_TERMINAL_CLONE_REPLACE"}
# A parameter with such a name carries a credential. Deliberately wide: a false hit costs a click in the
# console; a miss would let the model set a credential.
CREDENTIAL_NAME = re.compile(r"pass|secret|key|token|pwd|pin|cert|private|credential|auth|sign", re.IGNORECASE)
# CSV columns that identify the object or describe the clone, never a credential.
_CLONE_COLUMNS = {"bank_terminal_id", "provider_id", "name", "descriptor", "status", "new_bank_terminal_id"}


def type_class(task_type: str, sensitive: bool = False) -> int:
    """The class of a type alone; a sensitive type (the console says so) is a secret."""
    if sensitive:
        return SECRET
    return TYPE_CLASS.get(task_type, MONEY)


def _csv_rows(text: Optional[str]) -> List[Dict[str, str]]:
    if not text:
        return []
    sample = text.lstrip("﻿")
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    # A malformed line (more values than the header) is the console's to refuse; here it only must not crash:
    # extra values land under the None key as a list and are ignored.
    return [{k.strip(): (v or "").strip() for k, v in row.items() if isinstance(k, str) and not isinstance(v, list)}
            for row in csv.DictReader(io.StringIO(sample), delimiter=delimiter)]


def _changes_params(payload: Dict[str, str]) -> List[str]:
    raw = payload.get("changes")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(i.get("param") or "") for i in items if isinstance(i, dict)]


def _object_params(objects: Iterable[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for o in objects or []:
        out += [str(k) for k in (o.get("params") or {})]
    return out


def task_class(task_type: str, *, sensitive: bool = False, payload: Optional[Dict[str, str]] = None,
               csv_text: Optional[str] = None, objects: Optional[List[Dict[str, Any]]] = None) -> int:
    """The class of one task: the type's class, raised by what the task actually carries."""
    payload = payload or {}
    klass = type_class(task_type, sensitive)
    rows = _csv_rows(csv_text)

    status_key = ROUTING_ON.get(task_type)
    if status_key:
        values = [payload.get(status_key, "")] + [r.get("status", "") for r in rows]
        values += [str((o.get("params") or {}).get(status_key, "")) for o in objects or []]
        # A clone into a pool defaults to rows switched off; only an explicit «on» makes it a money path.
        if any(v.upper() in ("ENABLED", "ON", "Y") for v in values):
            klass = max(klass, MONEY)

    if task_type in CREDENTIALS:
        names = _changes_params(payload) + _object_params(objects or [])
        for r in rows:
            names += [k for k in r if k not in _CLONE_COLUMNS]
        if any(CREDENTIAL_NAME.search(n) for n in names if n):
            klass = SECRET
    return klass


def runs_here(klass: int) -> bool:
    """Whether this server runs a task of the class, or leaves it to a person in the console."""
    return klass == REVERSIBLE
