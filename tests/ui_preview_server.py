"""Local fake-data server used only for visual UI verification."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COURSE_SELECT_PORT", "8001")
os.environ.setdefault("COURSE_SELECT_NO_BROWSER", "1")
os.environ.setdefault("COURSE_SELECT_DB_PATH", str(ROOT / "tests" / "ui_preview.db"))

PREVIEW_PORT = int(os.getenv("COURSE_SELECT_PREVIEW_PORT", "8001"))
PREVIEW_LOGGED_OUT = os.getenv("COURSE_SELECT_PREVIEW_LOGGED_OUT", "").strip() == "1"
PREVIEW_PHASE = os.getenv("COURSE_SELECT_PREVIEW_PHASE", "preselection").strip().lower()
PREVIEW_CAPTCHA = os.getenv("COURSE_SELECT_PREVIEW_CAPTCHA", "ready").strip().lower()
PREVIEW_TASK = os.getenv("COURSE_SELECT_PREVIEW_TASK", "none").strip().lower()

import app  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402

if PREVIEW_LOGGED_OUT:
    config.student_id = ""
    config.token = ""
    config.combined_cookie = ""
    config.elective_batch_code = ""
    config.elective_batch_name = ""
else:
    # Screenshots and demos must never expose a full student number.
    config.student_id = "2024******"
    config.token = "preview-token"
    config.combined_cookie = "preview-cookie"
    config.campus_code = "01"
    config.campus_name = "粤海校区"
    if PREVIEW_PHASE == "closed":
        config.elective_batch_code = "PREVIEW-CLOSED"
        config.elective_batch_name = "补选已结束"
    elif PREVIEW_PHASE == "unknown":
        config.elective_batch_code = ""
        config.elective_batch_name = ""
    elif PREVIEW_PHASE == "automatic":
        config.elective_batch_code = "PREVIEW-AUTOMATIC"
        config.elective_batch_name = "补选阶段"
    else:
        config.elective_batch_code = "PREVIEW-PRESELECTION"
        config.elective_batch_name = "预选阶段"


def fake_fetch_vtoken_and_image(*_args) -> dict[str, str]:
    """Return a local placeholder; visual checks must never call the school."""
    if PREVIEW_CAPTCHA == "unavailable":
        raise app.logic.CaptchaUnavailableError("preview closed window")
    if PREVIEW_CAPTCHA == "invalid":
        raise app.logic.CaptchaResponseError("preview invalid response")
    if PREVIEW_CAPTCHA == "network":
        raise app.requests.ConnectionError("preview network failure")
    captcha_svg = """
    <svg xmlns="http://www.w3.org/2000/svg" width="250" height="80" viewBox="0 0 250 80">
      <rect width="250" height="80" fill="#f2f4f7"/>
      <path d="M12 23h226M12 57h226" stroke="#dfe2e8" stroke-width="1"/>
      <text x="125" y="47" text-anchor="middle" font-family="sans-serif" font-size="18"
            fill="#46505f">视觉预览验证码</text>
    </svg>
    """.strip()
    return {
        "vtoken": "preview-vtoken",
        "cookie": "route=preview-route; insert_cookie=preview-cookie",
        "imageUrl": f"data:image/svg+xml;charset=utf-8,{quote(captcha_svg)}",
    }


app.logic.fetch_vtoken_and_image = fake_fetch_vtoken_and_image


def fake_refresh_elective_batch(_student_id: str, _token: str) -> str:
    """Keep preview refreshes local and deterministic."""
    return config.elective_batch_name


app.refresh_elective_batch = fake_refresh_elective_batch


PREVIEW_TOTAL_COURSES = 16
PREVIEW_PAGE_SIZE = 10
PREVIEW_MATCH_NAMES = ["数据结构", "数据库系统", "大数据分析"]
PREVIEW_OTHER_NAMES = ["设计思维", "城市规划", "音乐鉴赏", "现代文学"]


def fake_query_courses(course_type: str, page: int):
    names = {
        "TJKC": "本班推荐",
        "FANKC": "方案内课程",
        "FAWKC": "方案外课程",
        "XGXK": "校公选课",
        "TYKC": "体育课程",
        "MOOC": "慕课",
    }
    if course_type == "FXKC":
        return False, "辅修课程暂不支持，如需选辅修课请前往学校官方选课系统", ""
    data = {
        "total_count": PREVIEW_TOTAL_COURSES,
        "courses": _preview_courses(course_type, page),
        "msg": "",
        "is_error": False,
    }
    return True, data, names.get(course_type, course_type)


def _preview_courses(course_type: str, page: int):
    """按页返回 16 门不重复课程；其中 12 门名称含“数据”，便于检验全目录搜索。

    学校接口每页固定 10 门：第 0 页返回 10 门，第 1 页返回剩余 6 门。
    """
    start = page * PREVIEW_PAGE_SIZE
    courses = []
    for index in range(start, min(start + PREVIEW_PAGE_SIZE, PREVIEW_TOTAL_COURSES)):
        if index % 4 == 3:
            course_name = PREVIEW_OTHER_NAMES[index % len(PREVIEW_OTHER_NAMES)]
        else:
            course_name = PREVIEW_MATCH_NAMES[index % len(PREVIEW_MATCH_NAMES)]
        number_label = f"{index + 1:02d}"
        campus_name = config.campus_name
        courses.append(
            {
                "tcList": [
                    {
                        "teaching_class_id": f"{course_type}-OPEN-{number_label}",
                        "is_mooc": "0",
                        "class_capacity": "60",
                        "teaching_place": f"1-18周 星期二 3-4节 {campus_name} 教学楼C{number_label}",
                        "course_index": "01班",
                        "teacher_name": "陈老师",
                        "sport_name": "",
                        "is_choose": "",
                        "course_total_number": "42",
                        "is_full": "",
                        "is_conflict": "",
                        "number_of_selected": "42",
                    },
                    {
                        "teaching_class_id": f"{course_type}-FULL-{number_label}",
                        "is_mooc": "0",
                        "class_capacity": "50",
                        "teaching_place": f"1-18周 星期四 5-6节 {campus_name} A{number_label}05",
                        "course_index": "02班",
                        "teacher_name": "林老师",
                        "sport_name": "",
                        "is_choose": "",
                        "course_total_number": "50",
                        "is_full": "1",
                        "is_conflict": "",
                        "number_of_selected": "50",
                    },
                ],
                "course_number": f"CS{300 + index}",
                "course_name": f"{course_name}（{number_label}）",
                "department_name": "计算机与软件学院",
                "campus_name": campus_name,
                "sport_name": "",
                "number": index + 1,
                "selected": False,
            }
        )
    return courses


app.query_courses = fake_query_courses


def fake_get_enrolled_courses():
    """预览用假已选课程数据（不访问学校系统）。"""
    return True, [
        {
            "teaching_class_id": "PREVIEW-01",
            "course_name": "计算机系统（3）-组成设计与综合实践",
            "teacher_name": "陈老师",
            "teaching_place": "1-18周 星期一 1-1节 致信楼S308综合实验中心",
            "credit": "4",
            "course_number": "CS305",
            "course_type_name": "方案内课程",
            "campus_name": "粤海校区",
        },
        {
            "teaching_class_id": "PREVIEW-02",
            "course_name": "人工智能导论",
            "teacher_name": "林老师",
            "teaching_place": "2-16周 星期三 7-9节 汇智楼A305",
            "credit": "3",
            "course_number": "CS410",
            "course_type_name": "校公选课",
            "campus_name": "丽湖校区",
        },
        {
            "teaching_class_id": "PREVIEW-03",
            "course_name": "计算机网络与应用",
            "teacher_name": "史老师",
            "teaching_place": "1-18周 星期四 11-13节 致理楼L1-606",
            "credit": "3",
            "course_number": "CS312",
            "course_type_name": "方案内课程",
            "campus_name": "粤海校区",
        },
        {
            "teaching_class_id": "PREVIEW-04",
            "course_name": "创新实践",
            "teacher_name": "王老师",
            "teaching_place": "1-8周 星期六 1-2节 创客中心201",
            "credit": "2",
            "course_number": "IN201",
            "course_type_name": "实践课程",
            "campus_name": "粤海校区",
        },
        {
            "teaching_class_id": "PREVIEW-05",
            "course_name": "专业实习",
            "teacher_name": "李老师",
            "teaching_place": "",
            "credit": "2",
            "course_number": "PR401",
            "course_type_name": "实践课程",
            "campus_name": "粤海校区",
        },
        {
            "teaching_class_id": "PREVIEW-06",
            "course_name": "计算机系统课程设计与体系结构综合实验",
            "teacher_name": "周老师",
            "teaching_place": "2-18周(双) 星期三 3-4节 致腾楼326综合实验室",
            "credit": "3",
            "course_number": "CS306",
            "course_type_name": "方案内课程",
            "campus_name": "粤海校区",
        },
        {
            "teaching_class_id": "PREVIEW-07",
            "course_name": "嵌入式计算平台设计与软硬件协同开发",
            "teacher_name": "吴老师",
            "teaching_place": "1-17周(单) 星期三 3-4节 致腾楼326创新实验室",
            "credit": "3",
            "course_number": "CS307",
            "course_type_name": "方案内课程",
            "campus_name": "粤海校区",
        },
    ]


app.get_enrolled_courses = fake_get_enrolled_courses


_preview_task_state = {
    "running": PREVIEW_TASK in {"running", "paused", "relogin"},
    "paused": PREVIEW_TASK == "paused",
    "pause_acknowledged": PREVIEW_TASK == "paused",
    "pause_reason": (
        "学校提示“当前时间不在选课开放时间范围内”，任务已自动暂停；开放后可点击继续"
        if PREVIEW_TASK == "paused"
        else ""
    ),
    "pause_source": "school_window" if PREVIEW_TASK == "paused" else "",
    "paused_at": "2026-08-28T14:28:25+08:00" if PREVIEW_TASK == "paused" else "",
    "stopping": False,
    "stopping_reason": "",
}
_original_get_session_snapshot = app.get_session_snapshot


def fake_get_session_snapshot():
    snapshot = _original_get_session_snapshot()
    if PREVIEW_TASK == "relogin":
        snapshot.update(
            {
                "relogin_in_progress": True,
                "relogin_status": "running",
                "relogin_message": "正在使用 OCR 自动重新登录，最多识别 50 张验证码",
                "relogin_started_at": "2026-08-28T14:31:08+08:00",
                "relogin_finished_at": "",
                "relogin_max_attempts": 50,
            }
        )
    return snapshot


def fake_get_enroll_task_state():
    return dict(_preview_task_state)


def fake_get_enroll_progress():
    running = bool(_preview_task_state["running"])
    paused = bool(_preview_task_state["paused"])
    message = (
        "等待学校恢复会话，课程和进度均已保留"
        if PREVIEW_TASK == "relogin"
        else "课容量已满，继续尝试"
    )
    if paused:
        message = str(_preview_task_state["pause_reason"])
    return {
        **_preview_task_state,
        "started_at": "2026-08-28T14:28:24+08:00",
        "finished_at": None if running else "2026-08-28T14:29:00+08:00",
        "courses": [
            {
                "id": "PREVIEW-TASK-01",
                "name": "计算机安全导论（林老师）",
                "type": "FANKC",
                "status": "ENROLLING" if running else "PENDING",
                "attempts": 37,
                "message": message,
            }
        ],
        "events": [
            {
                "ts": "2026-08-28T14:31:08+08:00",
                "level": "warn" if PREVIEW_TASK in {"paused", "relogin"} else "info",
                "message": message,
            }
        ],
        "counts": {
            "total": 1,
            "success": 0,
            "failed": 0,
            "active": 1,
        },
    }


def fake_pause_enroll_task(*_args, **_kwargs):
    if not _preview_task_state["running"]:
        return False, "当前没有正在运行的抢课任务"
    _preview_task_state.update(
        {
            "paused": True,
            "pause_acknowledged": True,
            "pause_reason": "用户已暂停抢课任务",
            "pause_source": "user",
            "paused_at": "2026-08-28T14:32:00+08:00",
        }
    )
    return True, "用户已暂停抢课任务"


def fake_resume_enroll_task():
    if not _preview_task_state["running"]:
        return False, "当前没有可继续的抢课任务"
    _preview_task_state.update(
        {
            "paused": False,
            "pause_acknowledged": False,
            "pause_reason": "",
            "pause_source": "",
            "paused_at": "",
        }
    )
    return True, "抢课任务已继续"


if PREVIEW_TASK != "none":
    preview_course = app.CartCourse(
        id="PREVIEW-TASK-01",
        type="FANKC",
        name="计算机安全导论（林老师）",
    )
    app.cart_service.add_course(preview_course)
    app.cart_service.update_status(preview_course.id, database.STATUS_IN_PROGRESS)
    app.get_session_snapshot = fake_get_session_snapshot
    app.get_enroll_task_state = fake_get_enroll_task_state
    app.get_enroll_progress = fake_get_enroll_progress
    app.pause_enroll_task = fake_pause_enroll_task
    app.resume_enroll_task = fake_resume_enroll_task


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app.app, host="127.0.0.1", port=PREVIEW_PORT)
