# SPDX-License-Identifier: Apache-2.0
"""A minimal MCP server over stdio (JSON-RPC 2.0, one message per line).

stdout carries the protocol only; anything else goes to stderr. Every tool says
in its annotations what it does to data: reads (the default), prepares (creates
a preview in the console, changes nothing else) or writes. A tool returns text
(markdown) and, when the client speaks a protocol version that has it,
structured content.

A server may ask the person a question through the client (elicitation) while a
tool runs — only when the client said it can; otherwise `ask` returns None and
the tool decides what to do without an answer.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from .errors import InsightsError
from .markdown import UNTRUSTED_NOTE
from .mask import mask_text

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
STRUCTURED_SINCE = "2025-06-18"


@dataclass
class ToolResult:
    text: str
    structured: Optional[Dict[str, Any]] = None


Handler = Callable[[Dict[str, Any]], ToolResult]


EFFECTS = ("read", "prepare", "write")
# What each effect means to a client, in the protocol's annotations.
ANNOTATIONS = {
    "read": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "prepare": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "write": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
}
# The first word of a description says the same to a person reading the tool list.
PREFIX = {"prepare": "PREPARES:", "write": "WRITES:"}


@dataclass
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Handler
    title: str = ""
    effect: str = "read"

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"tool {self.name}: effect {self.effect!r}")
        prefix = PREFIX.get(self.effect)
        if prefix and not self.description.startswith(prefix):
            self.description = f"{prefix} {self.description}"


@dataclass
class Server:
    name: str
    version: str
    instructions: str = ""
    tools: Dict[str, Tool] = field(default_factory=dict)
    _protocol: str = SUPPORTED_VERSIONS[0]
    _client_caps: Dict[str, Any] = field(default_factory=dict)
    # Set by serve() (or a test): send a message to the client, receive the next one.
    _send: Optional[Callable[[Dict[str, Any]], None]] = None
    _receive: Optional[Callable[[], Optional[Dict[str, Any]]]] = None
    _pending: List[Dict[str, Any]] = field(default_factory=list)
    _ask_seq: int = 0

    def add(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self.tools[tool.name] = tool

    # ---- protocol -------------------------------------------------------
    def _list(self) -> Dict[str, Any]:
        return {"tools": [{
            "name": t.name,
            "title": t.title or t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": dict(ANNOTATIONS[t.effect]),
        } for t in self.tools.values()]}

    # ---- asking the person ------------------------------------------------
    @property
    def can_ask(self) -> bool:
        return "elicitation" in self._client_caps and self._send is not None and self._receive is not None

    def ask(self, message: str, properties: Dict[str, Any], required: Optional[List[str]] = None
            ) -> Optional[Dict[str, Any]]:
        """Ask the person through the client. The answer's content if accepted; {} if declined or
        cancelled; None if the client cannot ask."""
        if not self.can_ask:
            return None
        self._ask_seq += 1
        ask_id = f"ask-{self._ask_seq}"
        requested: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            requested["required"] = required
        self._send({"jsonrpc": "2.0", "id": ask_id, "method": "elicitation/create",
                    "params": {"message": message, "requestedSchema": requested}})
        while True:
            incoming = self._receive()
            if incoming is None:
                return {}
            if incoming.get("id") == ask_id and "method" not in incoming:
                result = incoming.get("result") or {}
                if result.get("action") == "accept":
                    return dict(result.get("content") or {})
                return {}
            self._pending.append(incoming)    # anything else waits until the tool returns

    def _call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tool = self.tools.get(params.get("name", ""))
        if tool is None:
            return _error_result(f"unknown tool: {params.get('name')!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error_result("arguments must be an object")
        try:
            result = tool.handler(arguments)
        except InsightsError as exc:
            return _error_result(mask_text(str(exc)))
        except Exception as exc:   # boundary: a tool bug must not kill the server
            # the traceback carries the exception text too: masked like everything else that leaves
            print(mask_text(traceback.format_exc()), file=sys.stderr)
            return _error_result(f"{type(exc).__name__}: {mask_text(str(exc))[:500]}")
        out: Dict[str, Any] = {
            "content": [{"type": "text", "text": f"{result.text}\n\n{UNTRUSTED_NOTE}"
                         if UNTRUSTED_NOTE not in result.text else result.text}],
            "isError": False,
        }
        if result.structured is not None and self._protocol >= STRUCTURED_SINCE:
            out["structuredContent"] = _jsonable(result.structured)
        return out

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            asked = (message.get("params") or {}).get("protocolVersion")
            self._protocol = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            caps = (message.get("params") or {}).get("capabilities")
            self._client_caps = dict(caps) if isinstance(caps, dict) else {}
            result: Dict[str, Any] = {
                "protocolVersion": self._protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            }
            if self.instructions:
                result["instructions"] = self.instructions
        elif method == "tools/list":
            result = self._list()
        elif method == "tools/call":
            result = self._call(message.get("params") or {})
        elif method == "ping":
            result = {}
        elif isinstance(method, str) and method.startswith("notifications/"):
            return None
        else:
            if request_id is None:
                return None
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve(self) -> int:
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:   # UTF-8 everywhere; no CRLF translation on Windows
                reconfigure(encoding="utf-8", newline="")
        def send(message: Dict[str, Any]) -> None:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        def receive() -> Optional[Dict[str, Any]]:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    return message
            return None

        self._send, self._receive = send, receive
        while True:
            message = self._pending.pop(0) if self._pending else receive()
            if message is None:
                return 0
            response = self.handle(message)
            if response is not None:
                send(response)


def _error_result(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        out["required"] = required
    return out
