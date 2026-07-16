from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app
import config
import database
from database import DatabaseManager
from services import cart_service, course_service, enroll_service

client = TestClient(app.app)


class Resp:
    """轻量的伪学校响应，用于驱动抢课分类逻辑（不发真实请求）。"""

    def __init__(self, text, code="1", status=200):
        self.text = text
        self._code = code
        self.status_code = status

    def json(self):
        return {"code": self._code}


def _course(**overrides):
    values = {"id": "c1", "type": "FANKC", "name": "示例课程"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _prime_cart(monkeypatch, tmp_path, courses, status=database.STATUS_IN_PROGRESS):
    db = DatabaseManager(str(tmp_path / "grab.db"))
    monkeypatch.setattr(cart_service, "db", db)
    for course in courses:
        cart_service.add_course(course)
        cart_service.update_status(course.id, status)
    monkeypatch.setattr(config, "count", 3)
    monkeypatch.setattr(config, "delay", 0)
    return db


# ------------------------------------------------------------------
# 已选课程服务与接口
# ------------------------------------------------------------------


def test_get_enrolled_courses_maps_school_rows(monkeypatch):
    monkeypatch.setattr(config, "token", "t")
    monkeypatch.setattr(config, "combined_cookie", "c")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(
        course_service.choose_course,
        "query_enrolled_courses",
        lambda cookie, token, verbose=True: [
            {
                "courseName": "数据库系统",
                "teacherName": "陈老师",
                "teachingPlace": "H100",
                "credit": "3",
                "teachingClassID": "tc-1",
                "courseTypeName": "方案内课程",
            }
        ],
    )

    ok, data = course_service.get_enrolled_courses()
    assert ok
    assert data[0]["course_name"] == "数据库系统"
    assert data[0]["teacher_name"] == "陈老师"
    assert data[0]["teaching_class_id"] == "tc-1"
    assert data[0]["credit"] == "3"


def test_get_enrolled_courses_requires_session(monkeypatch):
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    ok, data = course_service.get_enrolled_courses()
    assert not ok and data == course_service.SESSION_EXPIRED


def test_get_enrolled_courses_detects_school_login_page(monkeypatch):
    monkeypatch.setattr(config, "token", "expired")
    monkeypatch.setattr(config, "combined_cookie", "expired")
    monkeypatch.setattr(
        course_service.choose_course,
        "query_enrolled_courses",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            course_service.choose_course.SchoolSessionExpiredError()
        ),
    )

    ok, data = course_service.get_enrolled_courses()
    assert not ok and data == course_service.SESSION_EXPIRED


def test_enrolled_endpoint_requires_login(monkeypatch):
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    assert client.get("/api/school/enrolled").status_code == 401


def test_enrolled_endpoint_returns_courses(monkeypatch):
    monkeypatch.setattr(config, "token", "t")
    monkeypatch.setattr(config, "combined_cookie", "c")
    monkeypatch.setattr(
        app,
        "get_enrolled_courses",
        lambda: (True, [{"course_name": "算法", "teaching_class_id": "1"}]),
    )
    response = client.get("/api/school/enrolled")
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["courses"][0]["course_name"] == "算法"


# ------------------------------------------------------------------
# 抢课分类逻辑
# ------------------------------------------------------------------


def test_success_marks_course_and_stops_requesting_it(tmp_path, monkeypatch):
    course = _course(id="ok1", name="成功课")
    db = _prime_cart(monkeypatch, tmp_path, [course])
    calls = []
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: calls.append(cid) or Resp("添加选课志愿成功"),
    )
    assert enroll_service.grab_courses([course]) is True
    assert db.get_courses_by_status(database.STATUS_SUCCESS)[0]["id"] == "ok1"
    # 成功后不再对该课程发请求（config.count=3 也只调用一次）
    assert calls == ["ok1"]


def test_terminal_error_marks_failed_and_stops(tmp_path, monkeypatch):
    course = _course(id="bad1", name="冲突课")
    db = _prime_cart(monkeypatch, tmp_path, [course])
    calls = []
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: calls.append(cid) or Resp("上课时间冲突", code="0"),
    )
    assert enroll_service.grab_courses([course]) is True
    assert db.get_courses_by_status(database.STATUS_FAILED)[0]["id"] == "bad1"
    assert calls == ["bad1"]


def test_capacity_full_keeps_retrying(tmp_path, monkeypatch):
    course = _course(id="full1", name="满员课")
    db = _prime_cart(monkeypatch, tmp_path, [course])
    calls = []
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: calls.append(cid) or Resp("该课程超过课容量", code="0"),
    )
    assert enroll_service.grab_courses([course]) is True
    # 一直重试：调用了 config.count 次，且课程仍处于抢课中（未终态）
    assert len(calls) == 3
    assert db.get_courses_by_status(database.STATUS_IN_PROGRESS)[0]["id"] == "full1"


def test_session_expired_returns_false_for_relogin(tmp_path, monkeypatch):
    course = _course(id="exp1", name="过期课")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp("login required", code="302", status=401),
    )
    assert enroll_service.grab_courses([course]) is False


def test_http_200_login_page_triggers_relogin(tmp_path, monkeypatch):
    course = _course(id="html-expired", name="过期登录页")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp(
            '<form action="student/check/login.do"><input name="vtoken">'
            '<input name="loginPwd"></form>',
            code="0",
            status=200,
        ),
    )

    assert enroll_service.grab_courses([course]) is False


def test_unknown_response_does_not_starve_other_courses(tmp_path, monkeypatch):
    """核心回归：修复旧代码 break 导致的多课程互相饿死问题。"""
    a = _course(id="A", name="未知返回课")
    b = _course(id="B", name="成功课")
    db = _prime_cart(monkeypatch, tmp_path, [a, b])
    monkeypatch.setattr(config, "count", 1)  # 单轮

    def fake(cid, ctype):
        if cid == "A":
            return Resp("系统繁忙，请稍后再试", code="0")
        return Resp("添加选课志愿成功")

    monkeypatch.setattr(enroll_service.choose_course, "submit_course_selection", fake)
    assert enroll_service.grab_courses([a, b]) is True
    # 即便 A 返回未知，B 在同一轮也能拿到抢课机会并成功
    assert db.get_courses_by_status(database.STATUS_SUCCESS)[0]["id"] == "B"


def test_multi_course_one_succeeds_other_continues(tmp_path, monkeypatch):
    a = _course(id="A", name="A课")
    b = _course(id="B", name="B课")
    db = _prime_cart(monkeypatch, tmp_path, [a, b])
    calls = []

    def fake(cid, ctype):
        calls.append(cid)
        if cid == "A":
            return Resp("添加选课志愿成功")
        return Resp("该课程超过课容量", code="0")

    monkeypatch.setattr(enroll_service.choose_course, "submit_course_selection", fake)
    assert enroll_service.grab_courses([a, b]) is True
    assert db.get_courses_by_status(database.STATUS_SUCCESS)[0]["id"] == "A"
    assert db.get_courses_by_status(database.STATUS_IN_PROGRESS)[0]["id"] == "B"
    assert calls.count("A") == 1  # 抢到后停止
    assert calls.count("B") == 3  # 未抢到持续尝试


# ------------------------------------------------------------------
# 进度跟踪
# ------------------------------------------------------------------


def test_progress_snapshot_reports_success_and_event(tmp_path, monkeypatch):
    course = _course(id="P1", name="进度课")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(config, "count", 1)
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp("添加选课志愿成功"),
    )

    enroll_service._reset_progress([course])
    enroll_service.grab_courses([course])
    snapshot = enroll_service.get_enroll_progress()

    assert snapshot["counts"]["success"] == 1
    assert snapshot["counts"]["total"] == 1
    assert snapshot["courses"][0]["status"] == database.STATUS_SUCCESS
    assert any("已加入我的课程" in event["message"] for event in snapshot["events"])


# ------------------------------------------------------------------
# 重登录续抢
# ------------------------------------------------------------------


def test_run_enroll_task_relogins_then_finishes(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "task.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="R1", name="续抢课"))  # PENDING

    grab_calls = []
    outcomes = [False, True]  # 首轮会话过期，重登录后成功

    def fake_grab(courses):
        grab_calls.append([c.id for c in courses])
        return outcomes[len(grab_calls) - 1]

    relogin_calls = []

    monkeypatch.setattr(enroll_service, "grab_courses", fake_grab)
    monkeypatch.setattr(
        enroll_service,
        "attempt_automatic_relogin",
        lambda max_attempts=config.ocr_relogin_max_attempts: relogin_calls.append(1) or (True, ""),
    )
    monkeypatch.setattr(config, "relogin_max_retries", 5)

    enroll_service.run_enroll_task(reserved=True)

    assert len(grab_calls) == 2
    assert len(relogin_calls) == 1
    assert grab_calls[0] == ["R1"]


def test_run_enroll_task_stops_after_consecutive_relogin_failures(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "task2.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="R2", name="失败续抢课"))

    monkeypatch.setattr(enroll_service, "grab_courses", lambda courses: False)
    relogin_calls = []
    monkeypatch.setattr(
        enroll_service,
        "attempt_automatic_relogin",
        lambda max_attempts=config.ocr_relogin_max_attempts: (
            relogin_calls.append(1) or (False, "OCR 失败")
        ),
    )
    monkeypatch.setattr(enroll_service.time, "sleep", lambda *_: None)
    monkeypatch.setattr(config, "relogin_max_retries", 3)

    enroll_service.run_enroll_task(reserved=True)

    # 连续失败达到阈值后停止
    assert len(relogin_calls) == 3
    assert db.get_courses_by_status(database.STATUS_FAILED)[0]["id"] == "R2"
