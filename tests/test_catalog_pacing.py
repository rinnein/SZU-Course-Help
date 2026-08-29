from __future__ import annotations

import pytest

from services.catalog_pacing import CatalogRequestPacer


def test_catalog_pacer_separates_starts_within_one_scope():
    now = [10.0]
    sleeps: list[float] = []

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = CatalogRequestPacer(clock=lambda: now[0], wait=advance)
    scope = ("2024110122", "batch", "01")

    assert pacer.wait_for_turn(scope, 600) == 0
    assert pacer.wait_for_turn(scope, 600) == pytest.approx(0.6)
    assert sleeps == pytest.approx([0.6])


def test_catalog_pacer_does_not_cross_account_batch_or_campus_scopes():
    now = [20.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    pacer = CatalogRequestPacer(clock=lambda: now[0], wait=advance)

    assert pacer.wait_for_turn(("student-a", "batch-a", "01"), 600) == 0
    assert pacer.wait_for_turn(("student-b", "batch-a", "01"), 600) == 0
    assert pacer.wait_for_turn(("student-a", "batch-b", "01"), 600) == 0
    assert pacer.wait_for_turn(("student-a", "batch-a", "02"), 600) == 0


def test_catalog_pacer_clear_removes_only_requested_scope():
    now = [30.0]
    waits: list[float] = []

    def advance(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    pacer = CatalogRequestPacer(clock=lambda: now[0], wait=advance)
    first = ("student-a", "batch", "01")
    second = ("student-b", "batch", "01")
    pacer.wait_for_turn(first, 600)
    pacer.wait_for_turn(second, 600)

    pacer.clear(first)
    assert pacer.wait_for_turn(first, 600) == 0
    assert pacer.wait_for_turn(second, 600) == pytest.approx(0.6)
    assert waits == pytest.approx([0.6])
