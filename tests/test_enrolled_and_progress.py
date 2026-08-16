from __future__ import annotations

from types import SimpleNamespace

import pytest
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


def test_generic_conflict_word_alone_is_not_terminal(tmp_path, monkeypatch):
    """回归：泛化的「冲突」一词不应误判为终态，避免误杀可重试课程。"""
    course = _course(id="conf1", name="泛冲突课")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp("此操作与系统策略无冲突", code="0"),
    )
    assert enroll_service.grab_courses([course]) is True
    # 仅含「冲突」但无具体时间冲突关键词 → 归类为 unknown，不会立即标 FAILED
    # 课程仍保持 ENROLLING（未被标 FAILED）
    assert not db_under(monkeypatch).get_courses_by_status(database.STATUS_FAILED)


def db_under(_monkeypatch):
    """Helper returning the cart_service.db used by current tests."""
    from services import cart_service

    return cart_service.db


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


# ------------------------------------------------------------------
# 任务取消与异常回退
# ------------------------------------------------------------------


def test_stop_enroll_task_stops_running_worker(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "stop.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="S1", name="待停课"))

    # 确保任务占用状态干净
    enroll_service._release_enroll_task()

    seen_stop = {"value": False}

    def fake_grab(courses):
        # 模拟用户在第一轮后请求停止
        assert enroll_service.is_enroll_task_running() is True
        result = enroll_service.stop_enroll_task()
        seen_stop["value"] = enroll_service.is_stop_requested()
        return result

    monkeypatch.setattr(enroll_service, "grab_courses", fake_grab)
    # reserved=False → 内部调用 reserve_enroll_task() 正确设置 _task_running
    enroll_service.run_enroll_task(reserved=False)

    assert seen_stop["value"] is True
    assert enroll_service.is_enroll_task_running() is False
    # 任务被用户停止后，活动课程回退为 PENDING（可重新启动）
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "S1"


def test_stop_enroll_task_returns_false_when_no_task_running():
    assert enroll_service.is_enroll_task_running() is False
    assert enroll_service.stop_enroll_task() is False


def test_abnormal_exit_reverts_in_progress_to_pending(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "crash.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="C1", name="崩溃课"))

    def raising_grab(courses):
        raise RuntimeError("模拟崩溃")

    monkeypatch.setattr(enroll_service, "grab_courses", raising_grab)

    with pytest.raises(RuntimeError, match="模拟崩溃"):
        enroll_service.run_enroll_task(reserved=True)

    # 异常中止后，ENROLLING 课程应回退为 PENDING，而非 FAILED
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "C1"
    assert not db.get_courses_by_status(database.STATUS_FAILED)


def test_normal_exit_marks_unresolved_as_failed(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "normal.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="N1", name="正常结束课"))

    # grab_courses 返回 True（流程结束），但课程仍处于 ENROLLING（未抢到）
    monkeypatch.setattr(
        enroll_service,
        "grab_courses",
        lambda courses: cart_service.update_status(
            courses[0].id, database.STATUS_IN_PROGRESS
        ) or True,
    )

    enroll_service.run_enroll_task(reserved=True)

    assert db.get_courses_by_status(database.STATUS_FAILED)[0]["id"] == "N1"


# ------------------------------------------------------------------
# 退避与熔断
# ------------------------------------------------------------------


def test_network_failures_back_off_and_circuit_break(tmp_path, monkeypatch):
    course = _course(id="NET1", name="网络课")
    db = _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(config, "count", 100)  # 足够多轮

    sleeps = []
    monkeypatch.setattr(enroll_service.time, "sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def always_failing(cid, ctype):
        call_count["n"] += 1
        raise ConnectionError("网络中断")

    monkeypatch.setattr(
        enroll_service.choose_course, "submit_course_selection", always_failing
    )

    assert enroll_service.grab_courses([course]) is True
    # 达到 MAX_NETWORK_FAILURES 后降级为 FAILED
    assert call_count["n"] == enroll_service.MAX_NETWORK_FAILURES
    assert db.get_courses_by_status(database.STATUS_FAILED)[0]["id"] == "NET1"
    # 熔断前的每次失败都退避；最后一次触发熔断后直接移出活动集，不再退避
    assert len(sleeps) == enroll_service.MAX_NETWORK_FAILURES - 1
    # 指数退避：首次退避为基准值
    assert sleeps[0] == pytest.approx(enroll_service.NETWORK_BACKOFF_BASE_MS / 1000.0)


def test_unknown_response_backoff_grows(tmp_path, monkeypatch):
    course = _course(id="UNK1", name="未知课")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(config, "count", 3)

    sleeps = []
    monkeypatch.setattr(enroll_service.time, "sleep", lambda s: sleeps.append(s))

    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp("系统繁忙，请稍后再试", code="0"),
    )

    enroll_service.grab_courses([course])
    # 未知分支每次都 sleep，且退避递增
    assert len(sleeps) == 3
    assert sleeps[1] > sleeps[0]
    assert sleeps[2] > sleeps[1]


# ------------------------------------------------------------------
# 事件队列上限与聚合指标
# ------------------------------------------------------------------


def test_event_queue_caps_at_max(tmp_path, monkeypatch):
    course = _course(id="E1", name="事件课")
    _prime_cart(monkeypatch, tmp_path, [course])
    monkeypatch.setattr(config, "count", 1)

    monkeypatch.setattr(
        enroll_service.choose_course,
        "submit_course_selection",
        lambda cid, ctype: Resp("添加选课志愿成功"),
    )

    enroll_service._reset_progress([course])
    # 灌入超过上限的事件
    for i in range(enroll_service.MAX_EVENTS + 50):
        enroll_service._add_event("info", f"事件 {i}")

    snapshot = enroll_service.get_enroll_progress()
    assert len(snapshot["events"]) == enroll_service.MAX_EVENTS
    # 保留的是最后 MAX_EVENTS 条
    assert snapshot["events"][-1]["message"] == f"事件 {enroll_service.MAX_EVENTS + 49}"


def test_progress_snapshot_reports_aggregates(tmp_path, monkeypatch):
    course = _course(id="A1", name="聚合课")
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

    assert snapshot["rounds"] == 1
    assert snapshot["total_requests"] == 1
    assert snapshot["counts"]["success"] == 1


# ------------------------------------------------------------------
# 并发 reserve
# ------------------------------------------------------------------


def test_reserve_enroll_task_is_exclusive(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "reserve.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(_course(id="X1", name="并发课"))

    # 重置任务占用状态
    enroll_service._release_enroll_task()
    assert enroll_service.reserve_enroll_task() is True
    assert enroll_service.reserve_enroll_task() is False
    assert enroll_service.is_enroll_task_running() is True
    enroll_service._release_enroll_task()
    assert enroll_service.is_enroll_task_running() is False
