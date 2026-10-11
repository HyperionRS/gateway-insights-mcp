# SPDX-License-Identifier: Apache-2.0
"""Secrets by reference: a tool gets `source:name`, the value never passes through the model.

The user describes sources in the profile; there are none by default:

    "secret_sources": {
      "op":    {"type": "1password"},                         # op read op://<name>
      "kc":    {"type": "keychain", "service": "gateway-insights"},
      "pass":  {"type": "pass"},                              # pass show <name>
      "env":   {"type": "env", "prefix": "GI_SECRET_"},       # environment variable <prefix><NAME>
      "files": {"type": "dir", "path": "~/.config/gateway-insights/secrets"},
      "corp":  {"type": "command", "argv": ["/usr/local/bin/get-secret", "--name", "{name}"]}
    }

`command` covers every other store (a cloud secret manager, a vault, a company tool): the command is
written by the user into the configuration and run as an argument list without a shell; the model only
supplies `{name}`, checked against NAME_RE. A `dir` source reads files inside its folder only, readable
by their owner only.

What the model sees of a resolved secret is its fingerprint: the last four characters and a short hash.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .errors import ConfigError, InputError

NAME_RE = re.compile(r"^[A-Za-z0-9._/-]{1,128}$")
SOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TYPES = ("env", "dir", "keychain", "1password", "pass", "command")
TIMEOUT = 20


@dataclass(frozen=True)
class Resolved:
    reference: str
    value: str

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.value.encode("utf-8")).hexdigest()[:8]
        return f"…{self.value[-4:]} (sha256 {digest})" if len(self.value) >= 8 else f"(sha256 {digest})"


def parse_reference(text: str) -> tuple:
    source, sep, name = str(text or "").partition(":")
    if not sep or not SOURCE_RE.match(source) or not NAME_RE.match(name) or ".." in name.split("/"):
        raise InputError("a secret reference is source:name — a source from the profile's secret_sources and "
                         "a name of letters, digits, '.', '_', '-', '/'")
    return source, name


def _run(argv, what: str) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except FileNotFoundError:
        raise ConfigError(f"{what}: {argv[0]!r} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise ConfigError(f"{what}: no answer in {TIMEOUT} s (locked? waiting for a password?)") from None
    if done.returncode != 0:
        # The store's own output stays out of the error: nothing guarantees it holds no secret.
        raise ConfigError(f"{what}: exit code {done.returncode}; run the store's command yourself to see why")
    value = done.stdout.rstrip("\r\n")
    if not value:
        raise ConfigError(f"{what}: empty value")
    return value


def resolve(reference: str, sources: Dict[str, Any]) -> Resolved:
    source, name = parse_reference(reference)
    spec = (sources or {}).get(source)
    if not isinstance(spec, dict):
        known = ", ".join(sorted(sources or {})) or "none configured"
        raise ConfigError(f"no secret source {source!r} in the profile (known: {known})")
    kind = spec.get("type")
    what = f"secret source {source!r}"
    if kind == "env":
        var = (str(spec.get("prefix") or "") + name).upper().replace("/", "_").replace("-", "_").replace(".", "_")
        value = os.environ.get(var, "")
        if not value:
            raise ConfigError(f"{what}: environment variable {var} is not set")
    elif kind == "dir":
        folder = Path(str(spec.get("path") or "")).expanduser().resolve()
        path = (folder / name).resolve()
        if folder not in path.parents:
            raise InputError(f"{what}: {name!r} points outside its folder")
        if not path.is_file():
            raise ConfigError(f"{what}: no file {name!r}")
        if os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError(f"{what}: {name!r} is readable by others than its owner. Run: chmod 600 {path}")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    elif kind == "keychain":
        value = _run(["security", "find-generic-password", "-s", str(spec.get("service") or "gateway-insights"),
                      "-a", name, "-w"], what)
    elif kind == "1password":
        value = _run(["op", "read", f"op://{name}"], what)
    elif kind == "pass":
        value = _run(["pass", "show", name], what).splitlines()[0]
    elif kind == "command":
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ConfigError(f"{what}: 'argv' must be a list of strings")
        value = _run([a.replace("{name}", name) for a in argv], what)
    else:
        raise ConfigError(f"{what}: type {kind!r} is not one of {', '.join(TYPES)}")
    if not value:
        raise ConfigError(f"{what}: empty value")
    return Resolved(f"{source}:{name}", value)


def looks_like_secret(value: str) -> bool:
    """A literal that looks like a key or a password rather than a reference or an ordinary value."""
    text = str(value or "").strip()
    if len(text) < 12 or " " in text:
        return False
    if re.match(r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9._/-]+$", text):     # a reference
        return False
    if re.match(r"^(?:sk|rk|pk)_(?:live|test)_|^(?:hgp|bt|ut|ghp|glpat)[_-]|^AKIA|^eyJ", text):
        return True
    classes = sum(bool(re.search(p, text)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    counts = Counter(text)
    entropy = -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())
    return classes >= 3 and entropy >= 3.5


ROTATE_WARNING = ("A value that looks like a secret was passed as text. It has already reached the model "
                  "provider and the conversation history: treat it as exposed and issue a new one, and pass "
                  "secrets as a reference to a configured source (source:name) next time.")
