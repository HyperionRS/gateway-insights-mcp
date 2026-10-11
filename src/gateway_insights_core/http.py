# SPDX-License-Identifier: Apache-2.0
"""The only way a server reaches the console: POST to an allowlisted path.

The client is built with the set of paths its server may call. A path outside
the set is refused before any socket is opened; the host, the query string and
the path are never taken from a tool argument.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, FrozenSet, List, Optional

from . import __version__
from .config import Profile
from .errors import AccessDenied, ApiError, ConfigError, InputError, NotSupported
from .mask import mask_json, mask_text

DEFAULT_TIMEOUT = 60.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
USER_AGENT = f"gateway-insights-mcp/{__version__}"


@dataclass
class Call:
    """One request as it went out: what «how obtained» prints."""
    path: str
    body: dict


@dataclass
class CallLog:
    calls: List[Call] = field(default_factory=list)

    def record(self, path: str, body: dict) -> None:
        self.calls.append(Call(path=path, body=body))


def _tls_context(profile: Profile) -> ssl.SSLContext:
    if not profile.verify_tls:
        print("WARNING: TLS verification is disabled for profile "
              f"{profile.name!r} (GI_INSECURE / verify_tls=false)", file=sys.stderr)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if profile.ca_bundle:
        path = os.path.expanduser(profile.ca_bundle)
        if not os.path.exists(path):
            raise ConfigError(f"ca_bundle not found: {path}")
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def _problem_text(body: str) -> str:
    """The console's error body, masked and cut, for the model to read."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return mask_text(body.strip())[:400]
    if isinstance(data, dict):
        parts = [str(data.get(k)) for k in ("title", "detail", "messageId") if data.get(k)]
        params = data.get("invalidParams")
        if isinstance(params, list):
            parts += [f"{p.get('name')}: {p.get('reason')}" for p in params if isinstance(p, dict)]
        if parts:
            return mask_text("; ".join(parts))[:400]
    return mask_text(json.dumps(mask_json(data)))[:400]


Opener = Callable[[urllib.request.Request, float, ssl.SSLContext], bytes]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the request carries the token, and the next address is the server's choice.

    Returning None makes urllib raise the 3xx as an HTTPError, which the client turns into an error.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request: urllib.request.Request, timeout: float, context: ssl.SSLContext) -> bytes:
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        return response.read()


class ConsoleClient:
    def __init__(self, profile: Profile, allowed_paths: FrozenSet[str], *,
                 timeout: float = DEFAULT_TIMEOUT, opener: Optional[Opener] = None,
                 log: Optional[CallLog] = None, max_calls: Optional[int] = None) -> None:
        self.profile = profile
        self._allowed = frozenset(allowed_paths)
        self._timeout = timeout
        self._opener = opener or _default_opener
        self._context: Optional[ssl.SSLContext] = None
        self.log = log or CallLog()
        # A client lives for one tool call: the cap bounds what one call may cost the console.
        self.max_calls = max_calls

    def post(self, path: str, body: dict, *, what: str, retry: bool = True, log_body: Optional[dict] = None) -> dict:
        """POST body to an allowlisted path; `what` names the call in error messages.

        retry=False for a write without an idempotency key: a retried write may happen twice.
        log_body replaces the body in the call log («how obtained») when the body carries a secret."""
        if path not in self._allowed:
            raise ConfigError(f"path {path!r} is not on this server's allowlist")
        if self.max_calls is not None and len(self.log.calls) >= self.max_calls:
            raise InputError(f"this request needs more than {self.max_calls} console calls; narrow it")
        self.log.record(path, body if log_body is None else log_body)
        if self._context is None:
            self._context = _tls_context(self.profile)
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {self.profile.token}",
        }
        url = f"{self.profile.base_url}{path}"
        for attempt in (0, 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                raw = self._opener(request, self._timeout, self._context)
                break
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace") if exc.fp else ""
                if exc.code in RETRY_STATUSES and attempt == 0 and retry:
                    time.sleep(1.5)
                    continue
                raise self._error(exc.code, text, what) from None
            except ssl.SSLCertVerificationError:
                raise ConfigError(f"TLS certificate of {self.profile.name!r} did not verify; "
                                  "set ca_bundle in the profile if the console uses a private CA") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 0 and retry:
                    time.sleep(1.5)
                    continue
                reason = getattr(exc, "reason", exc)
                raise ApiError(0, f"{what}: the console did not answer ({mask_text(str(reason))})") from None
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        return mask_json(parsed)

    def _error(self, status: int, body: str, what: str) -> ApiError:
        detail = _problem_text(body)
        if status == 401:
            return ApiError(status, f"{what}: the token was not accepted (expired, revoked or issued "
                                    f"by another console). Token {self.profile.token_hint}. {detail}")
        if status == 403:
            return AccessDenied(status, f"{what}: the token owner lacks the right for this read. {detail}")
        if status == 404:
            return NotSupported(status, f"{what}: this console does not have the endpoint — it is older "
                                        "than this tool needs")
        if 300 <= status < 400:
            return ApiError(status, f"{what}: the console answered with a redirect (HTTP {status}), which is "
                                    "not followed — the request carries the token. Set base_url to the address "
                                    "the console answers at directly.")
        if status == 400:
            return ApiError(status, f"{what}: the console refused the request: {detail}")
        return ApiError(status, f"{what}: console error HTTP {status}. {detail}")
