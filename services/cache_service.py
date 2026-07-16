"""HTTP cache policy for mutable local UI and API responses."""

from __future__ import annotations

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def get_no_cache_headers() -> dict[str, str]:
    """Return a copy so callers cannot mutate the shared policy."""
    return _NO_CACHE_HEADERS.copy()


__all__ = ["get_no_cache_headers"]
