from __future__ import annotations

import threading
import time

import requests
from fastapi.testclient import TestClient

import app
import config
import database
import logic
from database import DatabaseManager
from security import key_manager
from services import cart_service, enroll_service

client = TestClient(app.app)


def set_logged_session(monkeypatch, *, batch_code="batch", batch_name="预选阶段"):
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", batch_code)
    monkeypatch.setattr(config, "elective_batch_name", batch_name)


def test_health_and_static_login_page():
    assert client.get("/api/health").status_code == 200
    assert app.get_login_url().endswith(f"/login?ui={app.UI_CACHE_TOKEN}")
    response = client.get(f"/login?ui={app.UI_CACHE_TOKEN}")
    assert response.status_code == 200
    assert "Card Key V3" in response.text

    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["ui_cache_token"] == app.UI_CACHE_TOKEN
    assert bootstrap["ui_asset_build"] == app.UI_ASSET_BUILD


def test_captcha_api_reports_closed_window_without_generic_502(monkeypatch):
    monkeypatch.setattr(
        app.logic,
        "fetch_vtoken_and_image",
        lambda *_: (_ for _ in ()).throw(logic.CaptchaUnavailableError("closed")),
    )

    response = client.get("/api/captcha")

    assert response.status_code == 409
    assert response.json()["error_code"] == "CAPTCHA_UNAVAILABLE"
    assert response.json()["retryable"] is True
    assert "当前未提供登录验证码" in response.json()["message"]


def test_captcha_api_reports_finite_timeout(monkeypatch):
    monkeypatch.setattr(
        app.logic,
        "fetch_vtoken_and_image",
        lambda *_: (_ for _ in ()).throw(requests.Timeout("slow")),
    )

    response = client.get("/api/captcha")

    assert response.status_code == 504
    assert response.json()["error_code"] == "CAPTCHA_TIMEOUT"
    assert "本次加载已停止" in response.json()["message"]


def test_captcha_api_reports_network_failure(monkeypatch):
    monkeypatch.setattr(
        app.logic,
        "fetch_vtoken_and_image",
        lambda *_: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )

    response = client.get("/api/captcha")

    assert response.status_code == 503
    assert response.json()["error_code"] == "CAPTCHA_NETWORK_ERROR"
    assert response.json()["retryable"] is True


def test_captcha_api_reports_malformed_school_response(monkeypatch):
    monkeypatch.setattr(
        app.logic,
        "fetch_vtoken_and_image",
        lambda *_: (_ for _ in ()).throw(logic.CaptchaResponseError("bad payload")),
    )

    response = client.get("/api/captcha")

    assert response.status_code == 502
    assert response.json()["error_code"] == "CAPTCHA_INVALID_RESPONSE"
    assert "本次加载已停止" in response.json()["message"]


def test_courses_require_login(monkeypatch):
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    response = client.get("/api/school/courses?type=TJKC&page=1")
    assert response.status_code == 401


def test_courses_report_background_session_recovery_without_manual_login_prompt(monkeypatch):
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    monkeypatch.setattr(
        app,
        "get_session_snapshot",
        lambda: {
            "relogin_in_progress": True,
            "relogin_status": "running",
        },
    )
    monkeypatch.setattr(
        app,
        "get_enroll_task_state",
        lambda: {"running": True, "paused": False},
    )

    response = client.get("/api/school/courses?type=TJKC&page=1")

    assert response.status_code == 409
    assert response.json()["error_code"] == "SESSION_RECOVERY_IN_PROGRESS"
    assert response.json()["requires_manual_login"] is False


def test_session_uses_backend_phase_classification(monkeypatch):
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "batch")
    monkeypatch.setattr(config, "elective_batch_name", "预选阶段")

    body = client.get("/api/session").json()
    assert body["phase"] == config.PHASE_PRESELECTION
    assert body["automatic_enroll_allowed"] is False

    monkeypatch.setattr(config, "elective_batch_name", "补选阶段")
    body = client.get("/api/session").json()
    assert body["phase"] == config.PHASE_AUTOMATIC
    assert body["automatic_enroll_allowed"] is True

    monkeypatch.setattr(config, "elective_batch_name", "补选已结束")
    body = client.get("/api/session").json()
    assert body["phase"] == config.PHASE_CLOSED
    assert body["automatic_enroll_allowed"] is False


def test_course_api_converts_ui_pages_to_school_zero_based_pages(monkeypatch):
    observed_pages = []

    def fake_query(course_type, school_page):
        observed_pages.append((course_type, school_page))
        return (
            True,
            {
                "total_count": 0,
                "courses": [],
                "msg": "",
                "is_error": False,
            },
            "本班课程(推荐)",
        )

    set_logged_session(monkeypatch)
    monkeypatch.setattr(app, "query_courses", fake_query)

    first_page = client.get("/api/school/courses?type=TJKC&page=1&page_size=10")
    second_page = client.get("/api/school/courses?type=TJKC&page=2&page_size=10")

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert observed_pages == [("TJKC", 0), ("TJKC", 1)]


def test_closed_phase_does_not_query_school_course_endpoint(monkeypatch):
    set_logged_session(monkeypatch, batch_name="补选已结束")
    monkeypatch.setattr(
        app,
        "query_courses",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not query school")),
    )

    response = client.get("/api/school/courses?type=TJKC&page=1&page_size=10")

    assert response.status_code == 409
    assert response.json()["error_code"] == "COURSE_WINDOW_CLOSED"
    assert response.json()["retryable"] is True


def test_missing_batch_does_not_query_school_course_endpoint(monkeypatch):
    set_logged_session(monkeypatch, batch_code="", batch_name="")
    monkeypatch.setattr(
        app,
        "query_courses",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not query school")),
    )

    response = client.get("/api/school/courses?type=TJKC&page=1&page_size=10")

    assert response.status_code == 409
    assert response.json()["error_code"] == "BATCH_UNAVAILABLE"


def test_session_refresh_updates_phase_from_school_batch(monkeypatch):
    set_logged_session(monkeypatch, batch_name="预选阶段")

    def refresh_to_automatic(*_args):
        config.elective_batch_code = "fresh-batch"
        config.elective_batch_name = "补选阶段"
        return config.elective_batch_name

    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_automatic)

    response = client.post("/api/session/refresh")

    assert response.status_code == 200
    assert response.json()["phase"] == config.PHASE_AUTOMATIC
    assert response.json()["automatic_enroll_allowed"] is True


def test_session_refresh_clears_stale_batch_when_none_is_available(monkeypatch):
    set_logged_session(monkeypatch, batch_code="stale", batch_name="预选阶段")
    monkeypatch.setattr(
        app,
        "refresh_elective_batch",
        lambda *_args: (_ for _ in ()).throw(logic.ElectiveBatchUnavailableError("当前没有批次")),
    )

    response = client.post("/api/session/refresh")

    assert response.status_code == 409
    assert response.json()["error_code"] == "BATCH_UNAVAILABLE"
    assert response.json()["session"]["batch_code"] == ""
    assert config.elective_batch_code == ""
    assert config.elective_batch_name == ""


def test_session_refresh_reports_retryable_school_network_failure(monkeypatch):
    set_logged_session(monkeypatch)
    monkeypatch.setattr(
        app,
        "refresh_elective_batch",
        lambda *_args: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )

    response = client.post("/api/session/refresh")

    assert response.status_code == 503
    assert response.json()["error_code"] == "SCHOOL_NETWORK_ERROR"
    assert response.json()["retryable"] is True


def test_course_api_reports_retryable_school_timeout(monkeypatch):
    set_logged_session(monkeypatch)
    monkeypatch.setattr(
        app,
        "query_courses",
        lambda *_args: (_ for _ in ()).throw(requests.Timeout("slow")),
    )

    response = client.get("/api/school/courses?type=TJKC&page=1&page_size=10")

    assert response.status_code == 504
    assert response.json()["error_code"] == "SCHOOL_TIMEOUT"
    assert response.json()["retryable"] is True


def test_course_api_rejects_zero_ui_page():
    response = client.get("/api/school/courses?type=TJKC&page=0&page_size=10")
    assert response.status_code == 422


def test_login_route_validates_real_card_key_and_saves_mocked_school_session(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_SELECT_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setattr(config, "student_id", "")
    monkeypatch.setattr(config, "password", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    monkeypatch.setattr(config, "token", "mock-token")
    card_key = key_manager.generate_card_key("2024110122")
    monkeypatch.setattr(
        app,
        "perform_school_login",
        lambda *args: {
            "success": True,
            "cookie": "JSESSIONID=session; _WEU=weu",
            "name": "测试用户",
            "token": "mock-token",
        },
    )
    monkeypatch.setattr(app, "refresh_elective_batch", lambda *args: None)

    response = client.post(
        "/api/login",
        json={
            "student_id": "2024110122",
            "password": "school-password",
            "card_key": card_key,
            "vtoken": "vtoken",
            "verifyCode": [[10, 30], [40, 40], [80, 50], [120, 60]],
            "cookie": "route=route-value; Path=/, insert_cookie=insert-value; Path=/",
        },
    )

    assert response.status_code == 200
    assert response.json()["is_error"] is False
    assert config.student_id == "2024110122"
    assert config.password == "school-password"
    assert "JSESSIONID=session" in config.combined_cookie
    assert "route=route-value" in config.combined_cookie


def test_conflicting_course_is_rejected_by_api(tmp_path, monkeypatch):
    monkeypatch.setattr(cart_service, "db", DatabaseManager(str(tmp_path / "api-cart.db")))
    monkeypatch.setattr(app, "is_enroll_task_running", lambda: False)
    response = client.post(
        "/api/courses/add",
        json={
            "id": "conflict-1",
            "type": "FANKC",
            "name": "冲突课程",
            "is_conflict": "1",
        },
    )
    assert response.status_code == 200
    assert response.json()["is_error"] is True
    assert cart_service.get_courses_by_status("") == []


def test_preselection_cannot_start_enrollment(tmp_path, monkeypatch):
    monkeypatch.setattr(cart_service, "db", DatabaseManager(str(tmp_path / "api-enroll.db")))
    cart_service.add_course(type("Course", (), {"id": "c1", "type": "FANKC", "name": "课程"})())
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "batch")
    monkeypatch.setattr(config, "elective_batch_name", "复选阶段")

    def refresh_to_preselection(*_args):
        config.elective_batch_code = "fresh-batch"
        config.elective_batch_name = "预选阶段"
        return config.elective_batch_name

    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_preselection)
    monkeypatch.setattr(
        app,
        "start_enroll_worker",
        lambda: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post(
        "/api/enroll/courses",
        json={"confirmed_phase": True},
    )
    assert response.status_code == 409
    assert "预选" in response.json()["message"]


def test_enrollment_does_not_start_when_phase_cannot_be_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(cart_service, "db", DatabaseManager(str(tmp_path / "phase-error.db")))
    cart_service.add_course(type("Course", (), {"id": "c1", "type": "FANKC", "name": "课程"})())
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "cached-batch")
    monkeypatch.setattr(config, "elective_batch_name", "复选阶段")
    monkeypatch.setattr(
        app,
        "refresh_elective_batch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )
    monkeypatch.setattr(
        app,
        "start_enroll_worker",
        lambda: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/api/enroll/courses", json={"confirmed_phase": True})

    assert response.status_code == 503
    assert "未启动" in response.json()["message"]


def test_closed_batch_name_cannot_start_enrollment(tmp_path, monkeypatch):
    monkeypatch.setattr(cart_service, "db", DatabaseManager(str(tmp_path / "closed-phase.db")))
    cart_service.add_course(type("Course", (), {"id": "c1", "type": "FANKC", "name": "课程"})())
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "cached-batch")
    monkeypatch.setattr(config, "elective_batch_name", "补选阶段")

    def refresh_to_closed_phase(*_args):
        config.elective_batch_code = "fresh-batch"
        config.elective_batch_name = "补选已结束"
        return config.elective_batch_name

    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_closed_phase)
    monkeypatch.setattr(
        app,
        "start_enroll_worker",
        lambda: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/api/enroll/courses", json={"confirmed_phase": True})

    assert response.status_code == 409
    assert "未开放或已结束" in response.json()["message"]


def test_automatic_phase_starts_detached_enrollment_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(cart_service, "db", DatabaseManager(str(tmp_path / "start-worker.db")))
    cart_service.add_course(type("Course", (), {"id": "c1", "type": "FANKC", "name": "课程"})())
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")

    def refresh_to_automatic(*_args):
        config.elective_batch_code = "fresh-batch"
        config.elective_batch_name = "正选"
        return config.elective_batch_name

    starts = []
    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_automatic)
    monkeypatch.setattr(app, "start_enroll_worker", lambda: starts.append(1) or True)

    response = client.post("/api/enroll/courses", json={"confirmed_phase": True})

    assert response.status_code == 200
    assert response.json()["is_error"] is False
    assert starts == [1]


def test_worker_start_failure_preserves_pending_cart(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "worker-start-error.db"))
    monkeypatch.setattr(cart_service, "db", db)
    cart_service.add_course(type("Course", (), {"id": "c1", "type": "FANKC", "name": "课程"})())
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")

    def refresh_to_automatic(*_args):
        config.elective_batch_code = "fresh-batch"
        config.elective_batch_name = "正选"
        return config.elective_batch_name

    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_automatic)
    monkeypatch.setattr(
        app,
        "start_enroll_worker",
        lambda: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )

    response = client.post("/api/enroll/courses", json={"confirmed_phase": True})

    assert response.status_code == 500
    assert response.json()["error_code"] == "ENROLL_WORKER_START_FAILED"
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "c1"


def test_pause_and_resume_enrollment_api(monkeypatch):
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "batch")
    monkeypatch.setattr(config, "elective_batch_name", "正选")
    monkeypatch.setattr(app, "refresh_elective_batch", lambda *_args: "正选")

    assert enroll_service.reserve_enroll_task()
    try:
        paused = client.post("/api/enroll/pause")
        assert paused.status_code == 200
        assert paused.json()["progress"]["paused"] is True

        resumed = client.post("/api/enroll/resume")
        assert resumed.status_code == 200
        assert resumed.json()["progress"]["paused"] is False
    finally:
        enroll_service._release_enroll_task()


def test_resume_keeps_task_paused_when_school_phase_is_no_longer_automatic(monkeypatch):
    monkeypatch.setattr(config, "token", "token")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "batch")
    monkeypatch.setattr(config, "elective_batch_name", "正选")

    def refresh_to_preselection(*_args):
        config.elective_batch_code = "new-batch"
        config.elective_batch_name = "预选阶段"
        return config.elective_batch_name

    monkeypatch.setattr(app, "refresh_elective_batch", refresh_to_preselection)

    assert enroll_service.reserve_enroll_task()
    try:
        assert client.post("/api/enroll/pause").status_code == 200
        response = client.post("/api/enroll/resume")

        assert response.status_code == 409
        assert response.json()["error_code"] == "PHASE_NOT_ALLOWED"
        assert enroll_service.get_enroll_task_state()["paused"] is True
    finally:
        enroll_service._release_enroll_task()


def test_resume_requires_restored_school_login(monkeypatch):
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "combined_cookie", "")
    monkeypatch.setattr(config, "student_id", "2024110122")

    assert enroll_service.reserve_enroll_task()
    try:
        assert client.post("/api/enroll/pause").status_code == 200
        response = client.post("/api/enroll/resume")
        assert response.status_code == 409
        assert response.json()["error_code"] == "LOGIN_REQUIRED_FOR_RESUME"
    finally:
        enroll_service._release_enroll_task()


def test_failed_cart_course_can_be_requeued(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "retry-course.db"))
    monkeypatch.setattr(cart_service, "db", db)
    course = type("Course", (), {"id": "retry-1", "type": "FANKC", "name": "重试课程"})()
    assert cart_service.add_course(course)["success"]
    assert cart_service.update_status(course.id, database.STATUS_FAILED)

    response = client.post("/api/courses/retry?id=retry-1")

    assert response.status_code == 200
    assert response.json()["is_error"] is False
    assert db.get_courses_by_status(database.STATUS_NOT_STARTED)[0]["id"] == "retry-1"


def test_failed_cart_course_can_be_removed_after_task_finishes(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "remove-failed.db"))
    monkeypatch.setattr(cart_service, "db", db)
    course = type(
        "Course",
        (),
        {"id": "failed-1", "type": "FANKC", "name": "已停止课程"},
    )()
    assert cart_service.add_course(course)["success"]
    assert cart_service.update_status(course.id, database.STATUS_FAILED)
    enroll_service._reset_progress([course])
    enroll_service._update_course_progress(course.id, status=database.STATUS_FAILED)

    try:
        response = client.post("/api/courses/delete?id=failed-1")

        assert response.status_code == 200
        assert response.json()["is_error"] is False
        assert response.json()["progress"]["courses"] == []
        assert db.get_courses_by_status("") == []
    finally:
        enroll_service._set_progress_finished()


def test_delete_api_waits_for_safe_pause_boundary(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "remove-paused-api.db"))
    monkeypatch.setattr(cart_service, "db", db)
    course = type(
        "Course",
        (),
        {"id": "paused-1", "type": "FANKC", "name": "暂停课程"},
    )()
    assert cart_service.add_course(course)["success"]
    assert cart_service.update_status(course.id, database.STATUS_IN_PROGRESS)
    enroll_service._reset_progress([course])

    assert enroll_service.reserve_enroll_task()
    waiter = None
    try:
        assert enroll_service.pause_enroll_task()[0]
        blocked = client.post("/api/courses/delete?id=paused-1")
        assert blocked.status_code == 409
        assert blocked.json()["error_code"] == "ENROLL_TASK_PAUSE_PENDING"

        waiter = threading.Thread(target=enroll_service._wait_until_resumed)
        waiter.start()
        deadline = time.monotonic() + 2
        while (
            not enroll_service.get_enroll_task_state()["pause_acknowledged"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        removed = client.post("/api/courses/delete?id=paused-1")
        assert removed.status_code == 200
        assert removed.json()["task_stopping"] is True
        assert removed.json()["progress"]["courses"] == []
        assert db.get_courses_by_status("") == []
    finally:
        enroll_service._release_enroll_task()
        enroll_service._set_progress_finished()
        if waiter is not None:
            waiter.join(timeout=2)
