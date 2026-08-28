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
        "cardKey",
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
        "enrollProgress",
        "progressState",
        "progressNotice",
        "taskControlButton",
        "sessionRecoveryBanner",
        "recoveryTitle",
        "recoveryDetail",
        "refreshPhase",
        "refreshCourses",
    ):
        assert f'id="{control_id}"' in course
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in login
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in course
    assert f"/login.js?build={app.UI_ASSET_BUILD}" in login
    assert f"/course-app.js?build={app.UI_ASSET_BUILD}" in course


def test_login_captcha_ui_has_terminal_failure_states():
    login = (STATIC / "login.html").read_text(encoding="utf-8")
    script = (STATIC / "login.js").read_text(encoding="utf-8")

    assert 'data-state="idle"' in login
    assert "当前时段暂无验证码" in script
    assert 'code === "CAPTCHA_UNAVAILABLE"' in script
    assert "本次加载已经停止，不会在后台自动循环" in script
    assert "重新获取验证码" in script


def test_course_page_exposes_pause_and_relogin_states():
    script = (STATIC / "course-app.js").read_text(encoding="utf-8")

    assert '"/api/enroll/pause"' in script
    assert '"/api/enroll/resume"' in script
    assert "正在自动重新登录" in script
    assert "自动重新登录成功" in script
    assert "重新排队" in script
    assert "task_pause_acknowledged" in script
    assert "正在完成当前学校请求；安全暂停后即可移除" in script
    assert "taskStopping || (!terminalCourse && !canEditPausedTask)" in script
