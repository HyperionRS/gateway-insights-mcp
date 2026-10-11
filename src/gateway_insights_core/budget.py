# SPDX-License-Identifier: Apache-2.0
"""Load bounds for business tools, enforced in code.

A business tool may make at most MAX_CALLS console reads, at most
MAX_PARALLEL at a time. The budget raises before the call that would exceed it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Sequence, TypeVar

from .errors import InputError

MAX_CALLS = 8
MAX_PARALLEL = 3

T = TypeVar("T")


class CallBudget:
    def __init__(self, max_calls: int = MAX_CALLS) -> None:
        self.max_calls = min(max_calls, MAX_CALLS)
        self.used = 0

    def take(self, n: int = 1) -> None:
        if self.used + n > self.max_calls:
            raise InputError(f"this question needs more than {self.max_calls} console reads; "
                             "narrow it (fewer entities, a shorter list)")
        self.used += n

    def run(self, jobs: Sequence[Callable[[], T]]) -> List[T]:
        """Run independent reads, at most MAX_PARALLEL at a time; results in job order."""
        self.take(len(jobs))
        if len(jobs) <= 1:
            return [job() for job in jobs]
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            futures = [pool.submit(job) for job in jobs]
            return [f.result() for f in futures]
