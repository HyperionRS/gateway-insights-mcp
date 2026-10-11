# SPDX-License-Identifier: Apache-2.0
"""The frame every business tool runs in, whichever server it belongs to.

A business tool reaches the console only through its server's raw layer. CallRun bounds how many
reads it makes and how many run at once; the client's call log becomes «how obtained»; finish()
validates the report spec, renders markdown and, on request, an HTML page in the profile's theme.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, TypeVar

from . import report
from .budget import CallBudget
from .files import write_report
from .html import render_html
from .http import CallLog, ConsoleClient
from .mcp import ToolResult
from .theme import load_theme

T = TypeVar("T")

REPORT_PROP = {"report": {"type": "boolean", "description": "also render the answer as an HTML page in the "
                          "profile's theme and save it to the profile's report folder"}}


class CallRun:
    def __init__(self, client: ConsoleClient, raw_tool_of_path: Mapping[str, str], max_calls: int = 8) -> None:
        self.client = client
        self.raw_tool_of_path = raw_tool_of_path
        self.budget = CallBudget(max_calls)

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self.budget.take()
        return fn(*args, **kwargs)

    def parallel(self, *jobs: Callable[[], Any]) -> List[Any]:
        return self.budget.run(list(jobs))

    def provenance(self) -> List[Dict[str, str]]:
        return provenance_of(self.client.log, self.raw_tool_of_path)


PARAMS_CUT = 300


def provenance_of(log: CallLog, raw_tool_of_path: Optional[Mapping[str, str]] = None) -> List[Dict[str, str]]:
    """«How obtained»: every call as it went out — the body as the call log keeps it (a body with a secret
    is logged cleaned), serialised one way and cut at one length for every server."""
    out = []
    for c in log.calls:
        params = json.dumps(c.body, sort_keys=True, separators=(",", ":"), default=str)
        row = {"path": c.path, "params": params if len(params) <= PARAMS_CUT else params[:PARAMS_CUT - 3] + "..."}
        if raw_tool_of_path is not None:
            row = {"tool": raw_tool_of_path.get(c.path, "?"), **row}
        out.append(row)
    return out


def finish(run: CallRun, spec: Dict[str, Any], args: Dict[str, Any]) -> ToolResult:
    spec["provenance"] = run.provenance()
    report.validate(spec)
    text = report.to_markdown(spec)
    if args.get("report"):
        profile = run.client.profile
        page = render_html(spec, load_theme(profile.theme))
        path = write_report(page, spec["tool"], profile.report_dir, datetime.now())
        text += f"\n\nReport saved: {path}"
    return ToolResult(text, spec)
