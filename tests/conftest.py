"""Global test safety guards."""

from __future__ import annotations

import pytest
import requests

from services import course_cache_service
from services.catalog_pacing import catalog_request_pacer


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch):
    """Fail fast if a test reaches the real network."""

    def blocked_request(*args, **kwargs):
        raise AssertionError("Tests must mock every external HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)


@pytest.fixture(autouse=True)
def reset_catalog_pacing():
    """Keep process-wide pacing state from leaking between isolated tests."""
    catalog_request_pacer.clear()
    yield
    catalog_request_pacer.clear()


@pytest.fixture(autouse=True)
def isolate_catalog_cache(tmp_path, monkeypatch):
    """Never let tests read or write a user's real course cache."""
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "catalog-cache.json")
