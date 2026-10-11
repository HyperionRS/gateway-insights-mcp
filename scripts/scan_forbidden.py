# SPDX-License-Identifier: Apache-2.0
"""Release gate: fail if any tracked file, any version of a file in the history, a commit message or a
commit author contains a forbidden pattern.

The patterns are not in this repository — published, the list would itself
name what it keeps out. CI passes them in FORBIDDEN_PATTERNS (one regular
expression per line, case-insensitive), from a repository secret. A line that
legitimately matches carries the marker `forbidden-scan: allow`.

Without FORBIDDEN_PATTERNS (a fork, a local run) the scan is skipped with a
notice; set REQUIRE_FORBIDDEN_SCAN=1 to make that a failure.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ALLOW = "forbidden-scan: allow"


def git(*args: str) -> str:
    """git's output with file names as they are: core.quotePath would print a non-ASCII name as octal
    escapes, and a pattern for those letters would never see them."""
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def shown(name: str, patterns) -> str:
    """A file name as the public log may print it: hidden when the name itself is forbidden."""
    return "<a file whose name is forbidden>" if any(p.search(name) for p in patterns) else name


def scan_history_contents(patterns) -> int:
    """Scan every blob reachable from any ref, and every file name ever committed."""
    listing = git("rev-list", "--objects", "--all")
    paths = {}
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, set()).add(path)
    if not paths:
        return 0
    # One cat-file process for all objects; trees share the listing with blobs, so ask for the type.
    batch = subprocess.run(["git", "cat-file", "--batch"], input="\n".join(paths).encode() + b"\n",
                           check=True, capture_output=True).stdout
    hits, pos = 0, 0
    while pos < len(batch):
        end = batch.index(b"\n", pos)
        sha, kind, size = batch[pos:end].decode().split(" ")
        start = end + 1
        content = batch[start:start + int(size)]
        pos = start + int(size) + 1
        name = shown(sorted(paths[sha])[0], patterns)
        if kind != "blob":
            continue
        try:
            lines = content.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if ALLOW in line:
                continue
            for index, pattern in enumerate(patterns):
                if pattern.search(line):
                    print(f"history: blob {sha[:10]} ({name}):{number}: matches forbidden pattern #{index + 1}")
                    hits += 1
    # rev-list names an object once, by the first path it meets, so file names come from the log instead.
    names = git("log", "--all", "-z", "--name-only", "--format=")
    for path in sorted({n.strip("\n") for n in names.split("\0") if n.strip("\n")}):
        for index, pattern in enumerate(patterns):
            if pattern.search(path):
                # The name itself is what must not be published: point at it, do not print it.
                print(f"history: a committed file name matches forbidden pattern #{index + 1} "
                      "(git log --all --name-only lists them)")
                hits += 1
    return hits


def main() -> int:
    raw = os.environ.get("FORBIDDEN_PATTERNS", "")
    patterns = [re.compile(p.strip(), re.IGNORECASE) for p in raw.splitlines() if p.strip()]
    if not patterns:
        print("forbidden-pattern scan skipped: FORBIDDEN_PATTERNS is not set", file=sys.stderr)
        return 1 if os.environ.get("REQUIRE_FORBIDDEN_SCAN") == "1" else 0
    files = [f for f in git("ls-files", "-z").split("\0") if f]
    hits = 0
    for name in files:
        try:
            lines = open(name, encoding="utf-8").read().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            if ALLOW in line:
                continue
            for index, pattern in enumerate(patterns):
                if pattern.search(line):
                    # Print the pattern's index, not the pattern: the log is public.
                    print(f"{shown(name, patterns)}:{number}: matches forbidden pattern #{index + 1}")
                    hits += 1
    # History is published as well: every version of every file ever committed, not only the current one —
    # a line removed by a later commit is still in the published history.
    hits += scan_history_contents(patterns)
    # ... and the messages and author identities.
    log = git("log", "--all", "--format=%h%n%an <%ae>%n%cn <%ce>%n%B%n@@end")
    commit = "?"
    for line in log.splitlines():
        if line == "@@end":
            commit = "?"
            continue
        if commit == "?":
            commit = line.strip()
            continue
        for index, pattern in enumerate(patterns):
            if pattern.search(line):
                print(f"commit {commit}: message or author matches forbidden pattern #{index + 1}")
                hits += 1
    if hits:
        print(f"{hits} forbidden match(es)", file=sys.stderr)
        return 1
    print(f"forbidden-pattern scan: {len(files)} files and the history clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
