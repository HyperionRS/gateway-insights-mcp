# SPDX-License-Identifier: Apache-2.0
"""The journal: every preview, run and cancel this server made, at the user's, one JSON line each.

The console keeps the task and its history; the journal adds what the console does not know: that the
task came from this server, from which reads it was built (provenance), its class, and which secret
references it used — never a secret value.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

FILE_NAME = "actions-journal.jsonl"


def journal_path(report_dir: str) -> Path:
    return Path(report_dir).expanduser() / FILE_NAME


def record(report_dir: str, event: str, *, profile: str, console: str, task: Dict[str, Any],
           klass: Optional[int] = None,
           idempotency_key: Optional[str] = None, provenance: Optional[List[Dict[str, Any]]] = None,
           secret_refs: Optional[List[str]] = None, note: str = "") -> Path:
    folder = Path(report_dir).expanduser()
    if not folder.exists():
        folder.mkdir(parents=True)
        if os.name == "posix":
            os.chmod(folder, 0o700)
    path = folder / FILE_NAME
    entry = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event, "profile": profile, "console": console,
        "task_id": task.get("taskId"), "task_type": task.get("taskType"), "mode": task.get("mode"),
        "status": task.get("status"), "from_preview": task.get("fromPreviewTaskId"),
        "class": klass, "idempotency_key": idempotency_key,
        "secret_refs": secret_refs or [], "provenance": provenance or [], "note": note,
    }
    fresh = not path.exists()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    if fresh and os.name == "posix":
        os.chmod(path, 0o600)
    return path


def find_preview(report_dir: str, task_id: Any, *, console: str, profile: str,
                 task_type: str) -> Optional[Dict[str, Any]]:
    """The journal entry of a preview this server registered — for this console, profile and task type.

    A task id alone names different tasks in different consoles; an entry of another console, profile or
    type is not this task."""
    path = journal_path(report_dir)
    if not path.exists():
        return None
    found = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if (entry.get("event") == "preview" and entry.get("task_id") == task_id
                    and entry.get("console") == console and entry.get("profile") == profile
                    and entry.get("task_type") == task_type):
                found = entry
    return found
