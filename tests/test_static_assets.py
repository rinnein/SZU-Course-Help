from __future__ import annotations

from pathlib import Path

import app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static_dist"


def test_frontend_entrypoints_and_assets_exist():
    expected = {
        "login.html",
        "index.html",
        "styles.css",
        "login.js",
        "course-app.js",
        "bg.avif",
        "favicon.ico",
    }
    assert expected <= {path.name for path in STATIC.iterdir() if path.is_file()}


def test_login_and_course_pages_expose_required_controls():
    login = (STATIC / "login.html").read_text(encoding="utf-8")
    course = (STATIC / "index.html").read_text(encoding="utf-8")

    for control_id in (
        "studentId",
        "password",
        "captchaStage",
        "captchaStatusTitle",
        "captchaStatusDetail",
        "refreshCaptcha",
        "loginButton",
    ):
        assert f'id="{control_id}"' in login
    for control_id in (
        "categoryList",
        "courseList",
        "cartDialog",
        "openEnrollConfirm",
        "openMyCourses",
        "myCoursesDialog",
        "myCoursesScheduleWrap",
        "scheduleViewGrid",
        "scheduleViewList",
        "showPendingSwitch",
        "enrollProgress",
        "refreshPhase",
        "refreshCourses",
    ):
        assert f'id="{control_id}"' in course
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in login
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in course
    assert f"/login.js?build={app.UI_ASSET_BUILD}" in login
    assert f"/course-app.js?build={app.UI_ASSET_BUILD}" in course
    assert f"/schedule-parser.js?build={app.UI_ASSET_BUILD}" in course


def test_login_captcha_ui_has_terminal_failure_states():
    login = (STATIC / "login.html").read_text(encoding="utf-8")
    script = (STATIC / "login.js").read_text(encoding="utf-8")

    assert 'data-state="idle"' in login
    assert "当前时段暂无验证码" in script
    assert 'code === "CAPTCHA_UNAVAILABLE"' in script
    assert "本次加载已经停止，不会在后台自动循环" in script
    assert "重新获取验证码" in script


def test_schedule_view_assets_exist():
    """Verify the weekly schedule view has required symbols and styles."""
    course_js = (STATIC / "course-app.js").read_text(encoding="utf-8")
    parser_js = (STATIC / "schedule-parser.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    # Parser module exports
    assert "TIME_SLOTS" in parser_js
    assert "PERIODS" in parser_js
    assert "DAYS_OF_WEEK" in parser_js
    assert "parseScheduleSlots" in parser_js
    assert "MAX_PERIOD" in parser_js

    # Day regex must match "星期X" (two chars) and "周X" (one char)
    assert "(?:星期|周)" in parser_js

    # App uses schedule functions
    assert "renderMyCoursesSchedule" in course_js
    assert "switchMyCoursesView" in course_js
    assert "buildScheduleColorMap" in course_js
    assert "collectScheduleEntries" in course_js

    # Styles include schedule grid classes
    assert ".schedule-grid" in styles
    assert ".schedule-course" in styles
    assert ".schedule-nonstandard" in styles
    assert '.schedule-course[data-color="0"]' in styles
    assert '.schedule-course[data-color="11"]' in styles
    assert ".schedule-course.is-pending" in styles
    assert ".schedule-stack" in styles
    assert ".is-wide-schedule" in styles
    assert ".schedule-pending-toggle" in styles
