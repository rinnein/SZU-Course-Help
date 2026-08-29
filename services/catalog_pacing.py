"""Process-wide pacing for read-only school course-catalog requests."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import config

CatalogScope = tuple[str, str, str]


class CatalogRequestPacer:
    """Serialize request starts per account, batch, and campus."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] | None = None,
    ) -> None:
        self._clock = clock
        self._condition = threading.Condition()
        self._last_started: dict[CatalogScope, float] = {}
        self._wait = wait

    def wait_for_turn(self, scope: CatalogScope, delay_ms: int) -> float:
        """Wait until one scope may start, returning seconds actually waited."""
        minimum_gap = max(0, int(delay_ms)) / 1000
        waited = 0.0
        with self._condition:
            while True:
                now = self._clock()
                remaining = self._last_started.get(scope, float("-inf")) + minimum_gap - now
                if remaining <= 0:
                    self._last_started[scope] = now
                    return waited
                started_wait = now
                if self._wait is None:
                    self._condition.wait(timeout=remaining)
                else:
                    self._wait(remaining)
                waited += max(0.0, self._clock() - started_wait)

    def clear(self, scope: CatalogScope | None = None) -> None:
        """Discard pacing history after a session-context change or in tests."""
        with self._condition:
            if scope is None:
                self._last_started.clear()
            else:
                self._last_started.pop(scope, None)
            self._condition.notify_all()


catalog_request_pacer = CatalogRequestPacer()


def current_catalog_scope() -> CatalogScope:
    """Build a non-secret request scope from the active in-memory session."""
    return (
        str(config.student_id or ""),
        str(config.elective_batch_code or ""),
        str(config.campus_code or ""),
    )


def pace_catalog_request() -> float:
    """Apply the configured process-wide delay before one school request."""
    return catalog_request_pacer.wait_for_turn(
        current_catalog_scope(),
        config.catalog_page_delay_ms,
    )


__all__ = [
    "CatalogRequestPacer",
    "catalog_request_pacer",
    "current_catalog_scope",
    "pace_catalog_request",
]
