# SPDX-License-Identifier: Apache-2.0
"""Second-pass masking of everything the console returns.

The dashboards are not expected to return card numbers or secrets. The mask
runs anyway: a merchant name can contain anything, and a tool must not become
the way such a value leaves the console.
"""

from __future__ import annotations

import re
from typing import Any

# 13-19 digits, possibly split by spaces or dashes. Masked only when Luhn-valid.
_PAN_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

_SECRET_KEY_PARTS = ("password", "passwd", "secret", "token", "apikey", "api_key", "authorization",
                     "cvv", "cvc", "privatekey", "private_key")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_pan(match: re.Match) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not 13 <= len(digits) <= 19 or not _luhn_ok(digits):
        return raw
    return f"{digits[:6]}***{digits[-4:]}"


def mask_text(text: str) -> str:
    return _PAN_RE.sub(_mask_pan, text)


def _secret_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return any(part in k for part in _SECRET_KEY_PARTS)


def mask_json(value: Any) -> Any:
    """Mask a parsed JSON value in place-free fashion: returns a new value."""
    if isinstance(value, dict):
        return {k: ("***" if _secret_key(str(k)) and v not in (None, "") else mask_json(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask_json(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value
