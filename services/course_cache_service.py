"""Account-scoped persistent cache for read-only course catalog pages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from project_paths import data_dir

CACHE_SCHEMA_VERSION = 2
MAX_CACHE_ENTRIES = 2000
MAX_STALE_SECONDS = 7 * 24 * 60 * 60

_lock = threading.RLock()
_path = Path(data_dir() / "course_catalog_cache_v2.json")


@dataclass(frozen=True, slots=True)
class CatalogCacheScope:
    """Identity dimensions that make one catalog response reusable."""

    student_id: str
    batch_code: str
    campus_code: str

    def __post_init__(self) -> None:
        if not self.student_id or not self.batch_code or not self.campus_code:
            raise ValueError("complete student, batch, and campus scope is required")

    @property
    def digest(self) -> str:
        raw = "\0".join((self.student_id, self.batch_code, self.campus_code))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scope_from_snapshot(snapshot: dict[str, Any]) -> CatalogCacheScope:
    """Create a validated scope without persisting the raw student number."""
    return CatalogCacheScope(
        student_id=str(snapshot.get("student_id") or "").strip(),
        batch_code=str(snapshot.get("batch_code") or "").strip(),
        campus_code=str(snapshot.get("campus_code") or "").strip(),
    )


def _cache_key(
    scope: CatalogCacheScope,
    course_type: str,
    page: int,
    page_size: int,
) -> str:
    normalized_type = str(course_type or "").strip().upper()
    return f"{scope.digest}:{normalized_type}:{int(page)}:{int(page_size)}"


def _read() -> dict[str, Any]:
    try:
        value = json.loads(_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"schema": CACHE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA_VERSION:
        return {"schema": CACHE_SCHEMA_VERSION, "entries": {}}
    entries = value.get("entries")
    if not isinstance(entries, dict):
        value["entries"] = {}
    return value


def _write(value: dict[str, Any]) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="course-cache-",
        suffix=".tmp",
        dir=_path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        with suppress(OSError):
            os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, _path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)


def _valid_payload(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    courses = payload.get("courses")
    total_count = payload.get("total_count")
    return (
        isinstance(courses, list)
        and bool(courses)
        and all(isinstance(course, dict) for course in courses)
        and isinstance(total_count, int)
        and not isinstance(total_count, bool)
        and total_count >= len(courses)
        and payload.get("is_error") is False
    )


def _as_timestamp(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _prune(entries: dict[str, Any], now: float) -> None:
    oldest_allowed = now - MAX_STALE_SECONDS
    invalid_keys = [
        key
        for key, entry in entries.items()
        if not isinstance(entry, dict)
        or not isinstance(entry.get("payload"), dict)
        or _as_timestamp(entry.get("cached_at")) < oldest_allowed
    ]
    for key in invalid_keys:
        entries.pop(key, None)
    if len(entries) <= MAX_CACHE_ENTRIES:
        return
    ordered = sorted(
        entries,
        key=lambda key: _as_timestamp(entries[key].get("cached_at")),
    )
    for key in ordered[: len(entries) - MAX_CACHE_ENTRIES]:
        entries.pop(key, None)


def put_page(
    scope: CatalogCacheScope,
    course_type: str,
    page: int,
    page_size: int,
    payload: dict[str, Any],
) -> bool:
    """Atomically store one successful non-empty page under its exact scope."""
    if page < 1 or page_size < 1 or not _valid_payload(payload):
        return False
    now = time.time()
    key = _cache_key(scope, course_type, page, page_size)
    with _lock:
        data = _read()
        entries = data["entries"]
        entries[key] = {
            "scope_digest": scope.digest,
            "course_type": str(course_type or "").strip().upper(),
            "page": int(page),
            "page_size": int(page_size),
            "cached_at": now,
            "expires_at": now + config.catalog_cache_ttl_seconds,
            "payload": payload,
        }
        _prune(entries, now)
        try:
            _write(data)
        except (OSError, TypeError, ValueError):
            return False
    return True


def get_page(
    scope: CatalogCacheScope,
    course_type: str,
    page: int,
    page_size: int,
    *,
    allow_stale: bool = True,
) -> dict[str, Any] | None:
    """Return only an exact-scope page, annotated as read-only cached data."""
    key = _cache_key(scope, course_type, page, page_size)
    now = time.time()
    with _lock:
        entry = _read()["entries"].get(key)
    if not isinstance(entry, dict) or entry.get("scope_digest") != scope.digest:
        return None
    payload = entry.get("payload")
    if not _valid_payload(payload):
        return None
    cached_at = _as_timestamp(entry.get("cached_at"))
    expires_at = _as_timestamp(entry.get("expires_at"))
    if cached_at <= 0 or cached_at > now + 60 or cached_at < now - MAX_STALE_SECONDS:
        return None
    stale = expires_at <= now
    if stale and not allow_stale:
        return None
    return {
        **payload,
        "cached": True,
        "cache_read_only": True,
        "cache_stale": stale,
        "cached_at": cached_at,
        "cache_age_seconds": max(0, int(now - cached_at)),
    }


def has_page(
    scope: CatalogCacheScope,
    course_type: str,
    page: int,
    page_size: int,
) -> bool:
    return get_page(scope, course_type, page, page_size, allow_stale=True) is not None


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "CatalogCacheScope",
    "get_page",
    "has_page",
    "put_page",
    "scope_from_snapshot",
]
