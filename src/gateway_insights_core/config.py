# SPDX-License-Identifier: Apache-2.0
"""Profiles: where the console is, which token reads it, how reports look.

The file is JSON, readable by its owner only:

    {
      "default_profile": "prod",
      "profiles": {
        "prod": {
          "base_url": "https://console.example.com/console-api/api",
          "token": "<personal read-only API token>",
          "currency_id": null,
          "ca_bundle": null,
          "theme": "/path/to/theme-pack",
          "report_dir": "~/gateway-insights/reports"
        }
      }
    }

Environment variables override the file: GI_CONFIG (file path), GI_PROFILE,
GI_BASE_URL, GI_TOKEN, GI_CURRENCY_ID, GI_CA_BUNDLE, GI_THEME, GI_REPORT_DIR,
GI_INSECURE=1 (disables TLS verification; warned about on every start).

A profile for the actions server (which changes data) adds:

          "console_ui_url": "https://console.example.com/console",   # where the console opens; links
          "warn_orders": 100,          # warn above this many orders in one task
          "warn_entities": 50,         # ... and this many entities
          "show_customer_data": false, # customer names, cardholder, IP in order search results
          "secret_sources": {}         # see gateway_insights_core.secrets

GI_CONSOLE_UI_URL overrides console_ui_url.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "gateway-insights" / "config.json"
DEFAULT_REPORT_DIR = Path.home() / "gateway-insights" / "reports"

# A profile name may end up in log lines and file names: keep it boring.
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    token: str
    currency_id: Optional[int] = None
    ca_bundle: Optional[str] = None
    verify_tls: bool = True
    theme: Optional[str] = None
    report_dir: str = str(DEFAULT_REPORT_DIR)
    console_ui_url: Optional[str] = None
    warn_orders: int = 100
    warn_entities: int = 50
    show_customer_data: bool = False
    secret_sources: Dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def token_hint(self) -> str:
        """What diagnostics may show of the token: its first four and last four characters."""
        if len(self.token) <= 12:
            return "…" + self.token[-2:]
        return f"{self.token[:4]}…{self.token[-4:]}"


def config_path() -> Path:
    return Path(os.environ.get("GI_CONFIG", str(DEFAULT_CONFIG_PATH))).expanduser()


def _read_file() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    # On Windows st_mode does not reflect ACLs; the check would reject every file.
    if os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} is readable by others than its owner. Run: chmod 600 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("profiles") or {}, dict):
        raise ConfigError(f"{path}: expected an object with a 'profiles' object")
    return data


def profile_names() -> list:
    try:
        return sorted((_read_file().get("profiles") or {}).keys())
    except ConfigError:
        return []


def _optional_int(value, what: str) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{what} must be an integer, got {value!r}") from None


def check_token(token: str) -> None:
    """Accept a personal API token only: a browser session token is refused.

    A session token is a JWT (three dot-separated parts); it carries the whole session's rights and
    lifetime. A personal token is issued for this use and revoked on its own: read-only for the reading
    servers, read-write for the actions server in a profile of its own.
    """
    if not token:
        raise ConfigError("no token: set 'token' in the profile or GI_TOKEN")
    if token.count(".") == 2:
        raise ConfigError("this looks like a browser session token (JWT). Issue a personal API token in "
                          "the console profile — read-only for exec and decline, read-write for actions")
    if any(ch.isspace() for ch in token):
        raise ConfigError("the token contains whitespace; copy it again")


def load_profile(name: Optional[str] = None) -> Profile:
    data = _read_file()
    profiles = data.get("profiles") or {}
    profile_name = name or os.environ.get("GI_PROFILE") or data.get("default_profile")
    if not profile_name:
        profile_name = next(iter(profiles)) if len(profiles) == 1 else "default"
    profile_name = str(profile_name)
    if not PROFILE_NAME_RE.match(profile_name):
        raise ConfigError(f"invalid profile name {profile_name!r}: lowercase letters, digits, '-' and '_'")

    raw = profiles.get(profile_name) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"profile {profile_name!r} must be an object")
    if profiles and profile_name not in profiles and not os.environ.get("GI_BASE_URL"):
        known = ", ".join(sorted(profiles)) or "none"
        raise ConfigError(f"no profile {profile_name!r} in {config_path()}; known: {known}")

    def pick(key: str, env: str):
        return os.environ.get(env) or raw.get(key)

    base_url = str(pick("base_url", "GI_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError(f"profile {profile_name!r}: no base_url (or GI_BASE_URL)")
    if not base_url.startswith("https://"):
        raise ConfigError(f"profile {profile_name!r}: base_url must start with https://")
    if "?" in base_url or "#" in base_url:
        raise ConfigError(f"profile {profile_name!r}: base_url must not carry a query or fragment")

    token = str(pick("token", "GI_TOKEN") or "").strip()
    check_token(token)

    insecure = os.environ.get("GI_INSECURE") == "1" or raw.get("verify_tls") is False
    ui_url = str(pick("console_ui_url", "GI_CONSOLE_UI_URL") or "").strip().rstrip("/") or None
    if ui_url and not ui_url.startswith("https://"):
        raise ConfigError(f"profile {profile_name!r}: console_ui_url must start with https://")
    sources = raw.get("secret_sources") or {}
    if not isinstance(sources, dict):
        raise ConfigError(f"profile {profile_name!r}: secret_sources must be an object")
    return Profile(
        name=profile_name,
        base_url=base_url,
        token=token,
        currency_id=_optional_int(pick("currency_id", "GI_CURRENCY_ID"), "currency_id"),
        ca_bundle=pick("ca_bundle", "GI_CA_BUNDLE") or None,
        verify_tls=not insecure,
        theme=pick("theme", "GI_THEME") or None,
        report_dir=str(pick("report_dir", "GI_REPORT_DIR") or DEFAULT_REPORT_DIR),
        console_ui_url=ui_url,
        warn_orders=_optional_int(raw.get("warn_orders"), "warn_orders") or 100,
        warn_entities=_optional_int(raw.get("warn_entities"), "warn_entities") or 50,
        show_customer_data=raw.get("show_customer_data") is True,
        secret_sources=sources,
    )
