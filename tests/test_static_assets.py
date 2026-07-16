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

    for control_id in ("studentId", "password", "cardKey", "captchaStage", "loginButton"):
        assert f'id="{control_id}"' in login
    for control_id in (
        "categoryList",
        "courseList",
        "cartDialog",
        "openEnrollConfirm",
        "openMyCourses",
        "myCoursesDialog",
        "enrollProgress",
        "refreshPhase",
        "refreshCourses",
    ):
        assert f'id="{control_id}"' in course
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in login
    assert f"/styles.css?build={app.UI_ASSET_BUILD}" in course
    assert f"/login.js?build={app.UI_ASSET_BUILD}" in login
    assert f"/course-app.js?build={app.UI_ASSET_BUILD}" in course
