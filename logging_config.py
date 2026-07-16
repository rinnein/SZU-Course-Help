"""Shared logging configuration for CLI and direct server startup."""

from __future__ import annotations

import logging
import os

DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Configure application logging once without replacing existing handlers."""
    level_name = os.getenv("COURSE_SELECT_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        return
    logging.basicConfig(level=level, format=DEFAULT_LOG_FORMAT)
