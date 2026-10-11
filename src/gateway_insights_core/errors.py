# SPDX-License-Identifier: Apache-2.0
"""Errors a tool reports to the model as a readable message, not a traceback."""

from __future__ import annotations


class InsightsError(Exception):
    """Base class: the message is safe to show (already masked, no secrets)."""


class ConfigError(InsightsError):
    """The configuration is missing or cannot be used."""


class InputError(InsightsError):
    """A tool argument is invalid; the message says which and why."""


class ApiError(InsightsError):
    """The console answered with an error."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class AccessDenied(ApiError):
    """403: the token owner lacks a right for this read."""


class NotSupported(ApiError):
    """404 on an allowlisted path: the console is older than the tool needs."""


class ThemeError(ConfigError):
    """A theme pack is malformed or tries to load something it must not."""
