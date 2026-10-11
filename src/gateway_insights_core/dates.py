# SPDX-License-Identifier: Apache-2.0
"""Report windows: whole days, both ends inclusive, as the console screens take them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .errors import InputError

MAX_WINDOW_DAYS = 731   # the dashboards accept two years


@dataclass(frozen=True)
class Window:
    start: date
    end: date   # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_range(self) -> dict:
        return {"startDate": self.start.isoformat(), "endDate": self.end.isoformat()}

    def overlaps(self, other: "Window") -> bool:
        return self.start <= other.end and other.start <= self.end

    def __str__(self) -> str:
        return self.start.isoformat() if self.start == self.end else f"{self.start} .. {self.end}"


def parse_day(value: Optional[str], what: str) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise InputError(f"{what}: expected YYYY-MM-DD, got {value!r}") from None


def window(date_from: Optional[str], date_to: Optional[str], today: date,
           default_days: int = 7, what: str = "period", max_days: int = MAX_WINDOW_DAYS) -> Window:
    start = parse_day(date_from, f"{what} start")
    end = parse_day(date_to, f"{what} end")
    if end is None:
        end = today
    if start is None:
        start = end - timedelta(days=default_days - 1)
    if start > end:
        raise InputError(f"{what}: start {start} is after end {end}")
    span = (end - start).days + 1
    if span > max_days:
        raise InputError(f"{what}: {span} days is longer than {max_days}")
    return Window(start, end)


def base_window(base_from: Optional[str], base_to: Optional[str], current: Window,
                max_days: int = MAX_WINDOW_DAYS) -> Optional[Window]:
    """An explicit base, or None for «the window of the same length right before»."""
    if base_from in (None, "") and base_to in (None, ""):
        return None
    if base_from in (None, "") or base_to in (None, ""):
        raise InputError("base: give both base_from and base_to, or neither")
    base = window(base_from, base_to, current.end, what="base", max_days=max_days)
    if base.overlaps(current):
        raise InputError(f"base {base} overlaps the period {current}")
    return base


def implied_base(current: Window) -> Window:
    """The default base: the same number of days right before the period."""
    end = current.start - timedelta(days=1)
    return Window(end - timedelta(days=current.days - 1), end)
