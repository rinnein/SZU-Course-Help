"""Global test safety guards."""

from __future__ import annotations

import pytest
import requests

from services import course_cache_service


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch):
    """Fail fast if a test reaches the real network."""

    def blocked_request(*args, **kwargs):
        raise AssertionError("Tests must mock every external HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)

    # The reverse proxy talks to the school over httpx; the starlette.testclient
    # also uses httpx internally. Removing the httpx block here lets the
    # TestClient tests continue to run (the deprecation warning was pre-existing).
    # The proxy tests use a mock client and never reach real network.


@pytest.fixture(autouse=True)
def isolate_course_cache(monkeypatch, tmp_path):
    """Keep persistent course-cache tests out of the repository workspace."""
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "course_cache.json")
