"""SQLite persistence for the local enrollment cart."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Protocol
from weakref import WeakSet

from campus import DEFAULT_CAMPUS_CODE, get_campus
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
    campus_code: str
    campus_name: str
    credit: str


def _default_db_path() -> Path:
    configured = os.getenv("COURSE_SELECT_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return data_dir() / "course_enroll.db"


class DatabaseManager:
    """Small, thread-friendly SQLite repository for cart courses."""

    _instances: WeakSet[DatabaseManager] = WeakSet()

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(Path(db_path).resolve() if db_path else _default_db_path())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._instances.add(self)
        self.init_database()

    @classmethod
    def close_all(cls) -> None:
        """Close all live managers, primarily for orderly test/process cleanup."""
        for manager in list(cls._instances):
            manager.close()

    def _connect(self) -> sqlite3.Connection:
        """Return the shared, thread-safe connection.

        A single connection is reused across all calls with
        ``check_same_thread=False``; writes are serialized by ``self._lock``
        so callers always see a consistent snapshot.  WAL mode and a busy
        timeout keep concurrent readers from blocking.
        """
        if self._connection is None:
            connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 10000")
            self._connection = connection
        return self._connection

    def close(self) -> None:
        """Close the shared connection when this manager is no longer needed."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> DatabaseManager:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        """Best-effort cleanup for managers created outside a context manager."""
        connection = self._connection
        if connection is not None:
            with suppress(sqlite3.Error):
                connection.close()
            self._connection = None

    def init_database(self) -> None:
        """Create the cart table when it does not already exist."""
        with self._lock:
            connection = self._connect()
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    campus_code TEXT NOT NULL DEFAULT '01',
                    campus_name TEXT NOT NULL DEFAULT '粤海校区',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(courses)").fetchall()
            }
            if "campus_code" not in columns:
                connection.execute(
                    "ALTER TABLE courses ADD COLUMN campus_code TEXT NOT NULL DEFAULT '01'"
                )
            if "campus_name" not in columns:
                connection.execute(
                    "ALTER TABLE courses ADD COLUMN campus_name TEXT NOT NULL DEFAULT '粤海校区'"
                )
            for column in ("teaching_place", "course_name", "teacher_name", "credit"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE courses ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            migrations = {
                "auto_enabled": "ALTER TABLE courses ADD COLUMN auto_enabled INTEGER NOT NULL DEFAULT 1",
                "priority_group": "ALTER TABLE courses ADD COLUMN priority_group TEXT NOT NULL DEFAULT ''",
                "priority_rank": "ALTER TABLE courses ADD COLUMN priority_rank INTEGER NOT NULL DEFAULT 0",
                "course_number": "ALTER TABLE courses ADD COLUMN course_number TEXT NOT NULL DEFAULT ''",
                "time_signature": "ALTER TABLE courses ADD COLUMN time_signature TEXT NOT NULL DEFAULT ''",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    connection.execute(statement)
            connection.commit()

    def add_course(self, course: CartCourse) -> bool:
        """Insert or refresh one course and reset it to ``PENDING``."""
        try:
            now = datetime.now().isoformat(timespec="seconds")
            raw_campus_code = str(
                getattr(course, "campus_code", DEFAULT_CAMPUS_CODE) or DEFAULT_CAMPUS_CODE
            ).strip()
            selected_campus = get_campus(raw_campus_code)
            if selected_campus is None:
                return False
            teaching_place = str(getattr(course, "teaching_place", "") or "")
            course_name = str(getattr(course, "course_name", "") or "")
            teacher_name = str(getattr(course, "teacher_name", "") or "")
            credit = str(getattr(course, "credit", "") or "")
            auto_enabled = 1 if getattr(course, "auto_enabled", True) else 0
            priority_group = str(getattr(course, "priority_group", "") or "")
            priority_rank = int(getattr(course, "priority_rank", 0) or 0)
            course_number = str(getattr(course, "course_number", "") or "")
            time_signature = str(getattr(course, "time_signature", "") or "")
            with self._lock:
                connection = self._connect()
                connection.execute(
                    """
                    INSERT INTO courses (
                        id, type, name, campus_code, campus_name, status, updated_at,
                        teaching_place, course_name, teacher_name, credit, auto_enabled,
                        priority_group, priority_rank, course_number, time_signature
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type = excluded.type,
                        name = excluded.name,
                        campus_code = excluded.campus_code,
                        campus_name = excluded.campus_name,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        teaching_place = excluded.teaching_place,
                        course_name = excluded.course_name,
                        teacher_name = excluded.teacher_name,
                        credit = excluded.credit,
                        auto_enabled = excluded.auto_enabled,
                        priority_group = excluded.priority_group,
                        priority_rank = excluded.priority_rank,
                        course_number = excluded.course_number,
                        time_signature = excluded.time_signature
                    """,
                    (
                        course.id,
                        course.type,
                        course.name,
                        selected_campus.code,
                        selected_campus.name,
                        STATUS_NOT_STARTED,
                        now,
                        teaching_place,
                        course_name,
                        teacher_name,
                        credit,
                        auto_enabled,
                        priority_group,
                        priority_rank,
                        course_number,
                        time_signature,
                    ),
                )
                connection.commit()
            return True
        except (AttributeError, sqlite3.Error):
            logger.exception("Failed to add a course to the cart")
            return False

    def update_course_status(self, course_id: str, status: str) -> bool:
        """Update a course status when both the id and status are valid."""
        if not course_id or status not in VALID_STATUSES:
            return False
        try:
            with self._lock:
                connection = self._connect()
                cursor = connection.execute(
                    """
                    UPDATE courses
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, datetime.now().isoformat(timespec="seconds"), course_id),
                )
                connection.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception("Failed to update course status")
            return False

    def recover_interrupted_courses(self) -> int:
        """Return stale ``ENROLLING`` rows to ``PENDING`` after a prior crash."""
        try:
            with self._lock:
                connection = self._connect()
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
                connection.commit()
            if cursor.rowcount:
                logger.warning("Recovered %s interrupted cart course(s)", cursor.rowcount)
            return cursor.rowcount
        except sqlite3.Error:
            logger.exception("Failed to recover interrupted cart courses")
            return 0

    def update_course_preferences(self, course_id: str, **fields) -> bool:
        """Update safe, user-controlled queue preferences for one course."""
        allowed = {"auto_enabled", "priority_group", "priority_rank"}
        values = {key: fields[key] for key in fields if key in allowed}
        if not course_id or not values:
            return False
        if "auto_enabled" in values:
            values["auto_enabled"] = 1 if values["auto_enabled"] else 0
        if "priority_rank" in values:
            try:
                values["priority_rank"] = int(values["priority_rank"])
            except (TypeError, ValueError):
                return False
            if values["priority_rank"] < 0:
                return False
        if "priority_group" in values:
            values["priority_group"] = str(values["priority_group"] or "").strip()
        try:
            assignments = ", ".join(f"{key} = ?" for key in values)
            with self._lock:
                connection = self._connect()
                cursor = connection.execute(
                    f"UPDATE courses SET {assignments}, updated_at = ? WHERE id = ?",
                    (*values.values(), datetime.now().isoformat(timespec="seconds"), course_id),
                )
                connection.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception("Failed to update course preferences")
            return False

    def get_all_courses(self) -> list[dict]:
        """Return every cart row in unspecified order."""
        return self.get_courses_by_status("")

    def get_courses_by_status(self, status: str) -> list[dict]:
        """Return all rows, or only rows with one recognized status."""
        if status and status not in VALID_STATUSES:
            return []
        try:
            with self._lock:
                connection = self._connect()
                if status:
                    cursor = connection.execute("SELECT * FROM courses WHERE status = ?", (status,))
                else:
                    cursor = connection.execute("SELECT * FROM courses")
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            logger.exception("Failed to read cart courses")
            return []

    def get_active_courses(self) -> list[dict]:
        """Return rows that still need enrollment (PENDING or ENROLLING)."""
        try:
            with self._lock:
                connection = self._connect()
                cursor = connection.execute(
                    "SELECT * FROM courses WHERE status IN (?, ?)",
                    (STATUS_NOT_STARTED, STATUS_IN_PROGRESS),
                )
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            logger.exception("Failed to read active cart courses")
            return []

    def delete_course(self, course_id: str) -> bool:
        """Delete one course by teaching-class id."""
        if not course_id:
            return False
        try:
            with self._lock:
                connection = self._connect()
                cursor = connection.execute("DELETE FROM courses WHERE id = ?", (course_id,))
                connection.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            logger.exception("Failed to delete a cart course")
            return False

    def get_all_courses_sorted_by_time(self) -> list[dict]:
        """Return all cart rows in stable insertion order."""
        try:
            with self._lock:
                connection = self._connect()
                cursor = connection.execute("SELECT * FROM courses ORDER BY created_at ASC, id ASC")
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            logger.exception("Failed to read sorted cart courses")
            return []
