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
        "openTimetable",
        "timetableDialog",
        "timetableContent",
        "campusSelect",
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


def test_course_groups_start_collapsed_and_timetable_is_read_only():
    script = (STATIC / "course-app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "details.open" not in script
    assert '"/api/session/campus"' in script
    assert '"/api/school/enrolled"' in script
    assert "timetableEntriesWithLanes" in script
    assert "appendCluster" in script
    assert "fitTimetableRows" in script
    assert "block.dataset.periodSpan" in script
    assert "--timetable-row-height" in styles
    timetable_card_styles = styles[
        styles.index(".timetable-course {") : styles.index(".unscheduled-courses {")
    ]
    assert "overflow-wrap: anywhere" in timetable_card_styles
    assert "white-space: nowrap" not in timetable_card_styles
    assert (
        "/api/courses/add"
        not in script[
            script.index("function renderTimetable") : script.index("function renderTimetableError")
        ]
    )


def test_course_search_filters_full_catalog_and_repaginates():
    script = (STATIC / "course-app.js").read_text(encoding="utf-8")
    index = (STATIC / "index.html").read_text(encoding="utf-8")

    # 搜索不再局限于当前分页：旧的页内过滤文案必须移除
    assert "本页没有匹配结果" not in script
    assert "筛选本页课程" not in index
    assert "搜索全部课程" in index
    # 在完整目录上过滤，并按匹配结果重新分页
    assert "没有匹配的课程" in script
    assert "正在加载全部课程" in script
    assert "appState.searchPage" in script
    assert "results.slice(start, start + FILTER_PAGE_SIZE)" in script
    assert "匹配" in script
    # 完整目录缓存必须受会话范围约束，并能主动失效。
    assert "catalogScopeKey" in script
    assert "invalidateCatalogCache" in script
    assert "scopeKey !== catalogScopeKey()" in script
    # 多页读取有节流和明确上限，不能把截断响应当作完整目录。
    assert "CATALOG_PAGE_DELAY_MS" in script
    assert 'code: "CATALOG_PAGE_LIMIT"' in script
    assert "cache.courses.length !== cache.totalCount" in script


def test_course_catalog_fetch_paces_and_backs_off_throttling():
    script = (STATIC / "course-app.js").read_text(encoding="utf-8")

    # 分页间隔与限流退避常量：基础 600ms，被拒后自动加倍并重试
    assert "const CATALOG_PAGE_DELAY_MS = Number(catalogTiming.pageDelayMs) || 600" in script
    assert (
        "const CATALOG_PAGE_MAX_DELAY_MS = Number(catalogTiming.pageMaxDelayMs) || 2400" in script
    )
    assert (
        "const CATALOG_THROTTLE_BACKOFF_MS = Number(catalogTiming.throttleBackoffMs) || 2000"
        in script
    )
    assert (
        "const CATALOG_THROTTLE_MAX_RETRIES = Number(catalogTiming.throttleMaxRetries) || 3"
        in script
    )
    # 仅针对学校限流错误码退避重试，并给出进度提示
    assert 'error?.code !== "SCHOOL_COURSE_REJECTED"' in script
    assert "学校限流" in script
    assert "waitForCatalogDelay(catalogPacingDelayMs, controller.signal)" in script
