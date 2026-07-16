"""Resolve source, bundled-resource, and writable runtime paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def application_dir() -> Path:
    """Return the directory that owns runtime data for this installation."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def resource_path(relative_path: str | Path) -> Path:
    """Resolve a read-only resource in source and frozen application modes."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    base = Path(bundled_root).resolve() if bundled_root else application_dir()
    return (base / relative_path).resolve()


def data_dir() -> Path:
    """Return the writable data directory, optionally overridden by the user."""
    configured = os.getenv("COURSE_SELECT_DATA_DIR", "").strip()
    directory = Path(configured).expanduser().resolve() if configured else application_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory
