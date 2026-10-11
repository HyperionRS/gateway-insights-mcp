# SPDX-License-Identifier: Apache-2.0
"""The three rules a writing server lives by, in one place.

1. **Admission** — which class a task is when it is about to run. The class of the task as the console
   shows it now is the floor; what this server recorded when it prepared the task may only raise it,
   and counts only if it was recorded for the same console, profile, task id and type.
2. **Redaction** — what of a request may be shown or kept: every input a request carries (payload,
   objects with their parameters, CSV) is cleaned of credentials before it reaches «how obtained» or
   the journal. The console still receives the request as it is.
3. **Identity** — when two requests are the same operation: the real request, secrets included, keyed
   so that the key reveals nothing about a secret and two different secrets are two operations.

Admission and redaction err on the safe side: a credential-like name or value is hidden, a task whose
contents are not known here is not run here.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from gateway_insights_core import secrets
from gateway_insights_core.config import Profile

from . import classes, journal

HIDDEN = "<hidden>"
KEY_WINDOW_MINUTES = 10
# Per process: a key is stable for a repeated request (a double click), meaningless outside, and a
# secret in the request cannot be recovered from it.
_KEY_SECRET = os.urandom(32)


# ---- admission -----------------------------------------------------------------------------------

def admit(profile: Profile, task: Dict[str, Any], sensitive: bool = False) -> int:
    """The class a task is run as. Never lower than its type's class as the console shows the task now."""
    task_type = str(task.get("taskType") or "")
    floor = classes.type_class(task_type, sensitive)
    entry = journal.find_preview(profile.report_dir, task.get("taskId"), console=profile.base_url,
                                 profile=profile.name, task_type=task_type)
    if entry is not None:
        return max(floor, int(entry.get("class") or classes.MONEY))
    if task_type in classes.ROUTING_ON or task_type in classes.CREDENTIALS:
        # prepared elsewhere: whether it switches routing on or carries a credential is unknown here
        return max(floor, classes.MONEY)
    return floor


# ---- redaction -----------------------------------------------------------------------------------

def _secret_like(key: str, value: Any) -> bool:
    return bool(classes.CREDENTIAL_NAME.search(str(key or ""))) or secrets.looks_like_secret(str(value or ""))


class _Found:
    """Whether redaction hid anything — said by the redaction itself, not guessed from a difference."""

    def __init__(self) -> None:
        self.any = False

    def hide(self) -> str:
        self.any = True
        return HIDDEN


def _redact_csv(text: str, found: _Found) -> str:
    sample = str(text).lstrip("\ufeff")
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))
    if not rows:
        return sample
    header = rows[0]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerow(header)
    for row in rows[1:]:
        writer.writerow([found.hide() if cell and _secret_like(header[i] if i < len(header) else "", cell) else cell
                         for i, cell in enumerate(row)])
    return out.getvalue()


def _redact_changes(raw: Any, found: _Found) -> Any:
    """A `changes` value — a JSON list of {param, value} — wherever it appears."""
    try:
        changes = json.loads(raw)
    except (TypeError, ValueError):
        return found.hide() if secrets.looks_like_secret(str(raw)) else raw
    if not isinstance(changes, list):
        return raw
    local = _Found()
    cleaned = [dict(c, value=local.hide()) if isinstance(c, dict) and _secret_like(c.get("param"), c.get("value"))
               else c for c in changes]
    if not local.any:
        return raw                                   # untouched: kept exactly as given
    found.any = True
    return json.dumps(cleaned)


def _redact_params(params: Dict[str, Any], found: _Found, secret_field: Optional[str] = None) -> Dict[str, Any]:
    out = {}
    for k, v in params.items():
        if k == "changes" and v:
            out[k] = _redact_changes(v, found)
        elif (k == secret_field and v) or _secret_like(k, v):
            out[k] = found.hide()
        else:
            out[k] = v
    return out


def redact_found(body: Dict[str, Any], secret_field: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """(the request as «how obtained» and the journal may keep it, whether anything was hidden).

    The same rules for every input form: the payload, each object's parameters (a `changes` list
    included), and CSV cells by their column's name and their value."""
    found = _Found()
    shown = dict(body)
    if "payload" in body:
        shown["payload"] = _redact_params(dict(body.get("payload") or {}), found, secret_field)
    if body.get("objects"):
        shown["objects"] = [dict(o, params=_redact_params(dict(o["params"]), found))
                            if isinstance(o, dict) and o.get("params") else o for o in body["objects"]]
    if body.get("csv"):
        local = _Found()
        cleaned = _redact_csv(body["csv"], local)
        if local.any:                                # untouched CSV is kept exactly as given
            shown["csv"] = cleaned
            found.any = True
    return shown, found.any


def redact(body: Dict[str, Any], secret_field: Optional[str] = None) -> Dict[str, Any]:
    return redact_found(body, secret_field)[0]


def carries_secret(body: Dict[str, Any], secret_field: Optional[str] = None) -> bool:
    return redact_found(body, secret_field)[1]


# ---- identity ------------------------------------------------------------------------------------

def operation_key(prefix: str, body: Dict[str, Any], window: Optional[str] = None) -> str:
    """The same real request within ten minutes is the same operation; a different secret is not."""
    if window is None:
        now = datetime.now(timezone.utc)
        window = now.strftime("%Y%m%d%H") + f"{now.minute // KEY_WINDOW_MINUTES}"
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    digest = hmac.new(_KEY_SECRET, f"{window}|{canonical}".encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"gi-{prefix}-{window}-{digest}"
