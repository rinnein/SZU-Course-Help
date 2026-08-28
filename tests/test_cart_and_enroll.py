from __future__ import annotations

from types import SimpleNamespace

import config
import database
from database import DatabaseManager
from services import cart_service, enroll_service


def _course(**overrides):
    values = {
        "id": "class-1",
        "type": "FANKC",
        "name": "数据库系统 (陈老师)",
        "is_choose": "",
        "is_conflict": "",
        "is_full": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cart_blocks_chosen_and_conflicting_but_allows_full(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "cart.db"))
    monkeypatch.setattr(cart_service, "db", db)

    assert not cart_service.add_course(_course(is_choose="1"))["success"]
    assert not cart_service.add_course(_course(is_conflict="1"))["success"]
    assert cart_service.add_course(_course(is_full="1"))["success"]
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "class-1"


def test_interrupted_enrollment_rows_can_be_recovered(tmp_path):
    db = DatabaseManager(str(tmp_path / "recovery.db"))
    course = _course(id="stale")
    assert db.add_course(course)
    assert db.update_course_status(course.id, database.STATUS_IN_PROGRESS)

    assert db.recover_interrupted_courses() == 1
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "stale"


class FakeResponse:
    status_code = 200
    text = "添加选课志愿成功"

    def json(self):
        return {"code": "1"}


def test_grab_courses_uses_existing_request_function_and_marks_success(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "enroll.db"))
    monkeypatch.setattr(cart_service, "db", db)
    course = _course()
    assert cart_service.add_course(course)["success"]
    cart_service.update_status(course.id, database.STATUS_IN_PROGRESS)

    calls = []
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda class_id, course_type: calls.append((class_id, course_type)) or FakeResponse(),
    )
    monkeypatch.setattr(config, "count", 1)
    monkeypatch.setattr(config, "delay", 0)

    assert enroll_service.grab_courses([course]) == enroll_service.GrabOutcome.COMPLETED
    assert calls == [("class-1", "FANKC")]
    assert db.get_courses_by_status(database.STATUS_SUCCESS)[0]["id"] == "class-1"
