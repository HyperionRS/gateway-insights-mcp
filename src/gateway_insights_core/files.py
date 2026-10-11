# SPDX-License-Identifier: Apache-2.0
"""Writing a rendered report to the configured folder.

The file name is the tool name and a timestamp only — nothing from the console
ends up in a path.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from .errors import InputError

_TOOL = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def write_report(html: str, tool: str, report_dir: str, now: datetime) -> Path:
    if not _TOOL.match(tool):
        raise InputError(f"bad tool name for a report file: {tool!r}")
    folder = Path(report_dir).expanduser()
    if not folder.exists():
        folder.mkdir(parents=True)
        if os.name == "posix":   # only a folder we created: an existing one keeps its owner's mode
            os.chmod(folder, 0o700)
    path = folder / f"{tool}-{now.strftime('%Y%m%d-%H%M%S')}.html"
    path.write_text(html, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path
