"""Small persistent cache for successful, non-empty course catalog responses."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from project_paths import data_dir

_lock = threading.RLock()
_path = Path(
    os.getenv("COURSE_SELECT_CACHE_PATH", str(data_dir() / "course_cache.json"))
).expanduser()
FULL_CATALOG_TYPES = frozenset({"TJKC", "FANKC"})


def _read() -> dict[str, Any]:
    try:
        value = json.loads(_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(value: dict[str, Any]) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="course-cache-", suffix=".tmp", dir=_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, _path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name)


def cache_key(course_type: str, page: int, page_size: int) -> str:
    return f"{str(course_type).strip().upper()}:{int(page)}:{int(page_size)}"


def full_cache_key(course_type: str) -> str:
    return f"FULL:{str(course_type).strip().upper()}"


def put(course_type: str, page: int, page_size: int, payload: dict[str, Any]) -> bool:
    courses = payload.get("courses") if isinstance(payload, dict) else None
    if not isinstance(courses, list) or not courses:
        return False
    key = cache_key(course_type, page, page_size)
    with _lock:
        data = _read()
        try:
            version = int(data.get("version", 0) or 0) + 1
        except (TypeError, ValueError):
            version = 1
        entries = data.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            data["entries"] = entries
        entries[key] = {"cached_at": time.time(), "payload": payload, "version": version}
        data["version"] = version
        try:
            _write(data)
        except OSError:
            return False
    return True


def get(course_type: str, page: int, page_size: int) -> dict[str, Any] | None:
    key = cache_key(course_type, page, page_size)
    with _lock:
        entries = _read().get("entries", {})
        entry = entries.get(key) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return None
    return {
        **entry["payload"],
        "cached": True,
        "has_cache": True,
        "cached_at": entry.get("cached_at"),
        "cache_version": entry.get("version", 0),
    }


def put_full(course_type: str, payload: dict[str, Any]) -> bool:
    normalized_type = str(course_type).strip().upper()
    courses = payload.get("courses") if isinstance(payload, dict) else None
    if normalized_type not in FULL_CATALOG_TYPES or not isinstance(courses, list) or not courses:
        return False
    key = full_cache_key(normalized_type)
    with _lock:
        data = _read()
        try:
            version = int(data.get("version", 0) or 0) + 1
        except (TypeError, ValueError):
            version = 1
        entries = data.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            data["entries"] = entries
        entries[key] = {"cached_at": time.time(), "payload": payload, "version": version}
        data["version"] = version
        try:
            _write(data)
        except OSError:
            return False
    return True


def get_full(course_type: str) -> dict[str, Any] | None:
    normalized_type = str(course_type).strip().upper()
    if normalized_type not in FULL_CATALOG_TYPES:
        return None
    key = full_cache_key(normalized_type)
    with _lock:
        entries = _read().get("entries", {})
        entry = entries.get(key) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return None
    return {
        **entry["payload"],
        "cached": True,
        "full_catalog": True,
        "has_cache": True,
        "cached_at": entry.get("cached_at"),
        "cache_version": entry.get("version", 0),
    }


def annotate_live(
    payload: dict[str, Any], course_type: str, page: int, page_size: int
) -> dict[str, Any]:
    cached = get(course_type, page, page_size)
    return {
        **payload,
        "cached": False,
        "has_cache": cached is not None,
        "cached_at": cached.get("cached_at") if cached else None,
        "cache_version": cached.get("cache_version", 0) if cached else 0,
    }


__all__ = [
    "FULL_CATALOG_TYPES",
    "annotate_live",
    "cache_key",
    "full_cache_key",
    "get",
    "get_full",
    "put",
    "put_full",
]
