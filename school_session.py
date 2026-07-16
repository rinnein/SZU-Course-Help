"""Shared detection for expired school sessions."""

from __future__ import annotations

from typing import Any

SESSION_EXPIRED_CODES = frozenset({"302", "401", "403"})
SESSION_EXPIRED_HTTP_STATUSES = frozenset({302, 401, 403})


def looks_like_login_page(text: str) -> bool:
    """Return whether an HTML/text response resembles the school login page."""
    normalized = str(text or "").lower()
    return "student/check/login" in normalized or (
        "vtoken" in normalized and "loginpwd" in normalized
    )


def is_session_expired_response(
    *,
    status_code: int | None = None,
    code: Any = None,
    text: str = "",
) -> bool:
    """Classify explicit expiry codes and HTTP-200 login-page redirects."""
    return (
        status_code in SESSION_EXPIRED_HTTP_STATUSES
        or str(code) in SESSION_EXPIRED_CODES
        or looks_like_login_page(text)
    )


__all__ = [
    "SESSION_EXPIRED_CODES",
    "SESSION_EXPIRED_HTTP_STATUSES",
    "is_session_expired_response",
    "looks_like_login_page",
]
