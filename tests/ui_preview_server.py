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

import app  # noqa: E402
import config  # noqa: E402

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


def fake_fetch_vtoken_and_image() -> dict[str, str]:
    """Return a local placeholder; visual checks must never call the school."""
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
    course_name = {
        "TJKC": "计算机系统基础",
        "FANKC": "数据库系统",
        "FAWKC": "设计思维",
        "XGXK": "城市与文化",
        "TYKC": "羽毛球",
        "MOOC": "人工智能导论",
    }.get(course_type, "示例课程")
    data = {
        "total_count": 16,
        "courses": [
            {
                "tcList": [
                    {
                        "teaching_class_id": f"{course_type}-OPEN-01",
                        "is_mooc": "0",
                        "class_capacity": "60",
                        "teaching_place": "粤海校区 教学楼 C201 · 周二 3-4 节",
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
                        "teaching_class_id": f"{course_type}-FULL-02",
                        "is_mooc": "0",
                        "class_capacity": "50",
                        "teaching_place": "丽湖校区 A305 · 周四 5-6 节",
                        "course_index": "02班",
                        "teacher_name": "林老师",
                        "sport_name": "",
                        "is_choose": "",
                        "course_total_number": "50",
                        "is_full": "1",
                        "is_conflict": "",
                        "number_of_selected": "50",
                    },
                    {
                        "teaching_class_id": f"{course_type}-CONFLICT-03",
                        "is_mooc": "0",
                        "class_capacity": "45",
                        "teaching_place": "粤海校区 汇文楼 H102 · 周五 1-2 节",
                        "course_index": "03班",
                        "teacher_name": "王老师",
                        "sport_name": "",
                        "is_choose": "",
                        "course_total_number": "20",
                        "is_full": "",
                        "is_conflict": "1",
                        "number_of_selected": "20",
                    },
                ],
                "course_number": "CS305",
                "course_name": course_name,
                "department_name": "计算机与软件学院",
                "sport_name": "",
                "number": 1,
                "selected": False,
            },
            {
                "tcList": [
                    {
                        "teaching_class_id": f"{course_type}-CHOSEN-04",
                        "is_mooc": "0",
                        "class_capacity": "55",
                        "teaching_place": "粤海校区 南区实验楼 · 周三 7-8 节",
                        "course_index": "01班",
                        "teacher_name": "张老师",
                        "sport_name": "",
                        "is_choose": "1",
                        "course_total_number": "36",
                        "is_full": "",
                        "is_conflict": "",
                        "number_of_selected": "36",
                    }
                ],
                "course_number": "CS306",
                "course_name": f"{names.get(course_type, '课程')}专题实践",
                "department_name": "深圳大学",
                "sport_name": "",
                "number": 2,
                "selected": True,
            },
        ],
        "msg": "",
        "is_error": False,
    }
    return True, data, names.get(course_type, course_type)


app.query_courses = fake_query_courses


def fake_get_enrolled_courses():
    """预览用假已选课程数据（不访问学校系统）。"""
    return True, [
        {
            "teaching_class_id": "PREVIEW-01",
            "course_name": "计算机系统基础",
            "teacher_name": "陈老师",
            "teaching_place": "粤海校区 教学楼 C201 · 周二 3-4 节",
            "credit": "4",
            "course_number": "CS305",
            "course_type_name": "方案内课程",
        },
        {
            "teaching_class_id": "PREVIEW-02",
            "course_name": "人工智能导论",
            "teacher_name": "林老师",
            "teaching_place": "丽湖校区 A305 · 周四 5-6 节",
            "credit": "3",
            "course_number": "CS410",
            "course_type_name": "校公选课",
        },
    ]


app.get_enrolled_courses = fake_get_enrolled_courses


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app.app, host="127.0.0.1", port=PREVIEW_PORT)
