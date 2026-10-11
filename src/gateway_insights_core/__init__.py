# SPDX-License-Identifier: Apache-2.0
"""Shared core of the Gateway Insights MCP servers.

Configuration, the HTTP client with its path allowlist, masking and escaping,
number handling, the report spec with its HTML renderer and themes, a minimal
MCP stdio server, and the reads every server shares: entities, orders, routing,
restrictions. Secret sources for the actions server. Nothing here writes.
Standard library only.
"""

__version__ = "0.1.0"
