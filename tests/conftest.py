"""Global test safety guards."""

from __future__ import annotations

import pytest
import requests


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch):
    """Fail fast if a test reaches the real network."""

    def blocked_request(*args, **kwargs):
        raise AssertionError("Tests must mock every external HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)
