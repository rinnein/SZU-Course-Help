"""SQLite persistence for the local enrollment cart."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol

from project_paths import data_dir

logger = logging.getLogger(__name__)

STATUS_NOT_STARTED = "PENDING"
STATUS_IN_PROGRESS = "ENROLLING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
VALID_STATUSES = frozenset(
    {
        STATUS_NOT_STARTED,
        STATUS_IN_PROGRESS,
        STATUS_SUCCESS,
        STATUS_FAILED,
    }
)


class CartCourse(Protocol):
    """Minimum course shape accepted by the persistence layer."""

    id: str
    type: str
    name: str


def _default_db_path() -> Path:
    configured = os.getenv("COURSE_SELECT_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return data_dir() / "course_enroll.db"


class DatabaseManager:
    """Small, thread-friendly SQLite repository for cart courses."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(Path(db_path).resolve() if db_path else _default_db_path())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def init_database(self) -> None:
        """Create the cart table when it does not already exist."""
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    teaching_place TEXT NOT NULL DEFAULT '',
                    course_name TEXT NOT NULL DEFAULT '',
                    teacher_name TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Migrate existing databases that predate newer columns
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(courses)")}
            if "teaching_place" not in columns:
                connection.execute(
                    "ALTER TABLE courses ADD COLUMN teaching_place TEXT NOT NULL DEFAULT ''"
                )
            if "course_name" not in columns:
                connection.execute(
                    "ALTER TABLE courses ADD COLUMN course_name TEXT NOT NULL DEFAULT ''"
                )
            if "teacher_name" not in columns:
                connection.execute(
                    "ALTER TABLE courses ADD COLUMN teacher_name TEXT NOT NULL DEFAULT ''"
                )

    def add_course(self, course: CartCourse) -> bool:
        """Insert or refresh one course and reset it to ``PENDING``."""
        try:
            now = datetime.now().isoformat(timespec="seconds")
            teaching_place = str(getattr(course, "teaching_place", "") or "")
            course_name = str(getattr(course, "course_name", "") or "")
            teacher_name = str(getattr(course, "teacher_name", "") or "")
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO courses (id, type, name, status, updated_at, teaching_place, course_name, teacher_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type = excluded.type,
                        name = excluded.name,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        teaching_place = excluded.teaching_place,
                        course_name = excluded.course_name,
                        teacher_name = excluded.teacher_name
                    """,
                    (
                        course.id,
                        course.type,
                        course.name,
                        STATUS_NOT_STARTED,
                        now,
                        teaching_place,
                        course_name,
                        teacher_name,
                    ),
                )
            return True
        except (AttributeError, sqlite3.Error):
            logger.exception("Failed to add a course to the cart")
            return False

    def update_course_status(self, course_id: str, status: str) -> bool:
        """Update a course status when both the id and status are valid."""
        if not course_id or status not in VALID_STATUSES:
            return False
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE courses
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, datetime.now().isoformat(timespec="seconds"), course_id),
                )
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception("Failed to update course status")
            return False

    def recover_interrupted_courses(self) -> int:
        """Return stale ``ENROLLING`` rows to ``PENDING`` after a prior crash."""
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE courses
                    SET status = ?, updated_at = ?
                    WHERE status = ?
                    """,
                    (
                        STATUS_NOT_STARTED,
                        datetime.now().isoformat(timespec="seconds"),
                        STATUS_IN_PROGRESS,
                    ),
                )
            if cursor.rowcount:
                logger.warning("Recovered %s interrupted cart course(s)", cursor.rowcount)
            return cursor.rowcount
        except sqlite3.Error:
            logger.exception("Failed to recover interrupted cart courses")
            return 0

    def get_all_courses(self) -> list[dict]:
        """Return every cart row in unspecified order."""
        return self.get_courses_by_status("")

    def get_courses_by_status(self, status: str) -> list[dict]:
        """Return all rows, or only rows with one recognized status."""
        if status and status not in VALID_STATUSES:
            return []
        try:
            with self._connect() as connection:
                if status:
                    cursor = connection.execute("SELECT * FROM courses WHERE status = ?", (status,))
                else:
                    cursor = connection.execute("SELECT * FROM courses")
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            logger.exception("Failed to read cart courses")
            return []

    def delete_course(self, course_id: str) -> bool:
        """Delete one course by teaching-class id."""
        if not course_id:
            return False
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception("Failed to delete a cart course")
            return False

    def get_all_courses_sorted_by_time(self) -> list[dict]:
        """Return all cart rows in stable insertion order."""
        try:
            with self._connect() as connection:
                cursor = connection.execute("SELECT * FROM courses ORDER BY created_at ASC, id ASC")
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            logger.exception("Failed to read sorted cart courses")
            return []
