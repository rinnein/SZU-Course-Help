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
_path = Path(os.getenv("COURSE_SELECT_CACHE_PATH", str(data_dir() / "course_cache.json"))).expanduser()


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


def put(course_type: str, page: int, page_size: int, payload: dict[str, Any]) -> bool:
    courses = payload.get("courses") if isinstance(payload, dict) else None
    if not isinstance(courses, list) or not courses:
        return False
    key = cache_key(course_type, page, page_size)
    with _lock:
        data = _read()
        version = int(data.get("version", 0) or 0) + 1
        entries = data.setdefault("entries", {})
        entries[key] = {"cached_at": time.time(), "payload": payload, "version": version}
        data["version"] = version
        _write(data)
    return True


def get(course_type: str, page: int, page_size: int) -> dict[str, Any] | None:
    key = cache_key(course_type, page, page_size)
    with _lock:
        entry = _read().get("entries", {}).get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return None
    return {
        **entry["payload"],
        "cached": True,
        "has_cache": True,
        "cached_at": entry.get("cached_at"),
        "cache_version": entry.get("version", 0),
    }


def annotate_live(payload: dict[str, Any], course_type: str, page: int, page_size: int) -> dict[str, Any]:
    return {**payload, "cached": False, "has_cache": get(course_type, page, page_size) is not None}


__all__ = ["annotate_live", "cache_key", "get", "put"]
