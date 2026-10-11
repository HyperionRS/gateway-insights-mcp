# SPDX-License-Identifier: Apache-2.0
"""The raw layer of the actions server: one function per console endpoint.

Every change goes through a batch task: registered as a preview first, run only from that preview,
each register and run with an idempotency key. Single order operations and webhook sending are not on
the allowlist.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from gateway_insights_core import entities, ids, orders, restrictions, routing
from gateway_insights_core.errors import InputError
from gateway_insights_core.http import ConsoleClient

from . import guard
from .classes import CREDENTIAL_NAME

TYPES = "/batch-tasks/types"
FIELDS = "/batch-tasks/fields"
OBJECTS_COUNT = "/batch-tasks/objects-count"
REGISTER = "/batch-tasks/register"
RUN_PREVIEW = "/batch-tasks/run-preview"
VIEW = "/batch-tasks/view"
ITEMS_PAGE = "/batch-tasks/items-page"
TRANSITIONS = "/batch-tasks/transitions"
REASONS = "/batch-tasks/reasons"
LIST_PAGE = "/batch-tasks/list-page"
LIST_COUNTS = "/batch-tasks/list-counts"
CANCEL = "/batch-tasks/cancel"

BATCH_PATHS = frozenset({TYPES, FIELDS, OBJECTS_COUNT, REGISTER, RUN_PREVIEW, VIEW, ITEMS_PAGE, TRANSITIONS,
                         REASONS, LIST_PAGE, LIST_COUNTS, CANCEL})
# Values batch forms take from the console's dictionaries: reason codes of order operations (by code), and
# tariffs, which belong to a manager or a reseller (by id, given the owner).
BATCH_DICTIONARIES = {
    "chargeback_reasons": ("/order-operation/reasons-chargeback", None),
    "fraud_reasons": ("/order-operation/reasons-fraud", None),
    "reversal_reasons": ("/order-operation/reasons-reversal", None),
    "faux_reversal_reasons": ("/order-operation/reasons-faux-reversal", None),
    "manager_rate_plans": ("/autocomplete/manager-rate-plans", "manager_id"),
    "reseller_rate_plans": ("/autocomplete/reseller-rate-plans", "reseller_id"),
}
HOSTS = "/host/list"
ALLOWED_PATHS = (BATCH_PATHS | entities.PATHS | orders.SEARCH_PATHS | orders.DICTIONARY_PATHS
                 | frozenset(p for p, _ in BATCH_DICTIONARIES.values()) | routing.PATHS | restrictions.PATHS
                 | frozenset({HOSTS}))

SOURCES = ("SELECTION", "IDS", "CSV")
LEVELS = ("BANK_TERMINAL", "BLUEPRINT", "REQUESTOR", "MERCHANT")
ITEM_STATUSES = ("PENDING", "RUNNING", "DONE", "FAILED", "SKIPPED", "NEEDS_ATTENTION")
PRESETS = ("ACTIVE", "PROBLEM", "PREVIEWED", "DONE", "PARTIAL", "FAILED", "CANCELLED")
SETTLED = {"PREVIEWED", "DONE", "PARTIAL", "FAILED", "CANCELLED"}
MAX_OBJECTS = 5000
# Console calls one tool call may make, polling and paging included: a way back for the largest task
# reads 50 pages and prepares a few previews.
MAX_CALLS = 120
PREVIEW_WAIT = 30.0

FILTERS = ids.ENTITY_FILTERS
MAX_IDS = 100


MASKED = "******"


def mask_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """Was / will be of a credential, masked whatever the console says about the property.

    The console masks a property marked sensitive; one that is not marked (a password on a test console,
    a provider that never flagged it) comes back in clear. A field named like a credential is masked
    here regardless — a false hit hides a harmless value, a miss would hand a password to the model."""
    if CREDENTIAL_NAME.search(str(change.get("field") or "")):
        change = dict(change)
        for key in ("beforeValue", "afterValue"):
            if change.get(key) not in (None, ""):
                change[key] = MASKED
    return change


def sample_objects(refs: Any) -> str:
    """The console's examples of refused objects are their source lines — a CSV line with its credential
    columns. Only each line's first field, the object, is kept."""
    if not refs:
        return ""
    parts = [p.strip() for p in str(refs).replace("\n", ",").split(",") if p.strip()]
    return ", ".join(dict.fromkeys(p.split(";")[0].strip() for p in parts))


def mask_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("changes"):
        item = dict(item, changes=[mask_change(c) for c in item["changes"] if isinstance(c, dict)])
    return item


choice = ids.choice


def search_filters(args: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ids.search_filters(args, MAX_IDS)


class ActionsApi:
    def __init__(self, client: ConsoleClient, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self._sleep = sleep
        self._clock = clock

    # ---- reading ------------------------------------------------------------
    def types(self, object_type: Optional[str] = None) -> List[Dict[str, Any]]:
        body = {"objectType": object_type} if object_type else {}
        return list(self.client.post(TYPES, body, what="batch types").get("types") or [])

    def fields(self, task_type: str, provider_id: Optional[int] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"taskType": task_type}
        if provider_id is not None:
            body["providerId"] = provider_id
        return self.client.post(FIELDS, body, what="batch type fields")

    def objects_count(self, selection: Dict[str, Any]) -> int:
        data = self.client.post(OBJECTS_COUNT, {"filter": selection}, what="batch selection count")
        return int(data.get("count") or 0)

    def dictionary(self, name: str, query: str = "", owner_id: Optional[int] = None) -> Dict[str, Any]:
        if name not in BATCH_DICTIONARIES:
            raise InputError(f"dictionary: one of {', '.join(BATCH_DICTIONARIES)}")
        path, owner = BATCH_DICTIONARIES[name]
        body: Dict[str, Any] = {"offset": 0, "size": 100, "searchText": str(query or "")[:100]}
        if owner:
            if owner_id is None:
                raise InputError(f"{name} needs {owner}: tariffs belong to a {owner.split('_')[0]}")
            body["parentId"] = int(owner_id)
        return self.client.post(path, body, what=name.replace("_", " "))

    def hosts(self, manager_id: Optional[int] = None, query: str = "") -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {"offset": 0, "size": 100, "searchText": str(query or "")[:100]}
        if manager_id is not None:
            body["managerList"] = [{"id": int(manager_id), "name": ""}]
        return list(self.client.post(HOSTS, body, what="hosts").get("hosts") or [])

    def view(self, task_id: int) -> Dict[str, Any]:
        return self.client.post(VIEW, {"taskId": int(task_id)}, what="batch task")

    def items_page(self, task_id: int, status: Optional[str] = None, error_code: Optional[str] = None,
                   offset: int = 0, size: int = 50) -> Dict[str, Any]:
        body: Dict[str, Any] = {"taskId": int(task_id), "offset": int(offset), "size": int(size)}
        if status:
            body["status"] = choice(status, ITEM_STATUSES, "status")
        if error_code:
            body["errorCode"] = str(error_code)[:128]
        data = self.client.post(ITEMS_PAGE, body, what="batch task items")
        # An item's inputRef is its source line as given — for a CSV with credential columns, the
        # credentials themselves. Nothing here needs it, so it never leaves this function.
        data["items"] = [mask_item({k: v for k, v in it.items() if k != "inputRef"})
                         for it in data.get("items") or [] if isinstance(it, dict)]
        return data

    def transitions(self, task_id: int) -> List[Dict[str, Any]]:
        return [mask_change(t) for t in self.client.post(TRANSITIONS, {"taskId": int(task_id)},
                                                         what="batch task transitions").get("transitions") or []
                if isinstance(t, dict)]

    def reasons(self, task_id: int) -> List[Dict[str, Any]]:
        data = self.client.post(REASONS, {"taskId": int(task_id)}, what="batch task reasons")
        return [dict(r, sampleRefs=sample_objects(r.get("sampleRefs"))) for r in data.get("reasons") or []
                if isinstance(r, dict)]

    def list_page(self, scope: str = "MINE", preset: Optional[str] = None, query: Optional[str] = None,
                  offset: int = 0, size: int = 20) -> Dict[str, Any]:
        body: Dict[str, Any] = {"scope": choice(scope, ("MINE", "ALL"), "scope"), "offset": int(offset),
                                "size": int(size)}
        if preset:
            body["preset"] = choice(preset, PRESETS, "preset")
        if query:
            body["query"] = str(query)[:100]
        return self.client.post(LIST_PAGE, body, what="batch task list")

    def list_counts(self, scope: str = "MINE") -> Dict[str, Any]:
        return self.client.post(LIST_COUNTS, {"scope": choice(scope, ("MINE", "ALL"), "scope")},
                                what="batch task counts")

    # ---- changing -------------------------------------------------------------
    def register_preview(self, task_type: str, source: str, *, objects: Optional[List[Dict[str, Any]]] = None,
                         selection: Optional[Dict[str, Any]] = None, csv_text: Optional[str] = None,
                         payload: Optional[Dict[str, str]] = None, secret_field: Optional[str] = None
                         ) -> Tuple[Dict[str, Any], bool, str]:
        """Register a PREVIEW. (task view, duplicate, idempotency key).

        The key is the real request's (guard.operation_key: two secrets, two operations; the key reveals
        none); the call log keeps the request cleaned of credentials (guard.redact)."""
        source = choice(source, SOURCES, "source")
        body: Dict[str, Any] = {"taskType": task_type, "mode": "PREVIEW", "source": source,
                                "payload": dict(payload or {})}
        if source == "SELECTION":
            if not selection:
                raise InputError("source SELECTION needs a selection (level and filters)")
            body["filter"] = selection
            body["objects"] = []
        elif source == "IDS":
            if not objects:
                raise InputError("source IDS needs objects")
            if len(objects) > MAX_OBJECTS:
                raise InputError(f"at most {MAX_OBJECTS} objects in one task")
            body["objects"] = objects
        else:
            if not csv_text:
                raise InputError("source CSV needs csv")
            body["csv"] = csv_text
            body["objects"] = []
        key = guard.operation_key("preview", body)
        body["idempotencyKey"] = key
        shown = guard.redact(body, secret_field)
        data = self.client.post(REGISTER, body, what="batch preview", log_body=shown)
        return dict(data.get("task") or {}), bool(data.get("duplicate")), key

    def wait(self, task_id: int, timeout: float = PREVIEW_WAIT) -> Dict[str, Any]:
        """Poll the task until it settles or the time is up; the last view either way."""
        deadline = self._clock() + timeout
        delay = 0.5
        while True:
            task = self.view(task_id)
            if task.get("status") in SETTLED or self._clock() >= deadline:
                return task
            self._sleep(delay)
            delay = min(delay * 1.6, 3.0)

    def run_preview(self, preview_task_id: int) -> Tuple[Dict[str, Any], bool, str]:
        key = f"gi-run-{int(preview_task_id)}"
        data = self.client.post(RUN_PREVIEW, {"previewTaskId": int(preview_task_id), "idempotencyKey": key},
                                what="batch run")
        return dict(data.get("task") or {}), bool(data.get("duplicate")), key

    def cancel(self, task_id: int) -> Dict[str, Any]:
        return self.client.post(CANCEL, {"taskId": int(task_id)}, what="batch cancel")
