"""
抢课服务模块

职责：
    1. 执行抢课循环（遍历购物车课程，调用学校选课接口）
    2. 对学校返回结果分类处理（成功 / 容量满重试 / 终态失败 / 会话过期）
    3. 多门课程轮询抢课：抢到的立即停止，未抢到的继续
    4. 会话过期自动重登录，仅在连续多次重登录失败后才停止
    5. 线程安全的进度与事件跟踪，供 Web UI 轮询

核心约束：
    - 不修改 choose_course.submit_course_selection() 的请求格式和参数
    - 不修改学校返回结果的判断关键词
    - 保留 PENDING → ENROLLING → SUCCESS/FAILED 的状态语义
"""

import copy
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import choose_course
import config
import database
from school_session import is_session_expired_response
from services import cart_service
from services.auth_service import (
    attempt_automatic_relogin,
)

_task_state_lock = threading.Lock()
_task_running = False
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrollmentCourse:
    """Minimal immutable course data used by the background worker."""

    id: str
    type: str
    name: str


# ====================================================================
# 学校返回结果分类关键词
# ====================================================================
# 抢到成功
SUCCESS_KEYWORD = "添加选课志愿成功"
# 容量已满：属于可重试情形，继续下一轮
CAPACITY_FULL_KEYWORD = "该课程超过课容量"
# 终态失败：重试也不会成功，直接标记 FAILED 并移出活动集
TERMINAL_ERROR_KEYWORDS = (
    "已经选过",
    "已选过",
    "已经选择",
    "重复选课",
    "已存在",
    "时间冲突",
    "上课时间冲突",
    "冲突",
    "不在选课时间",
    "不在补选",
    "选课未开始",
    "选课已结束",
    "已结束",
    "超过学分",
    "学分已满",
    "已达上限",
    "已达到上限",
    "不满足",
    "无权限",
    "不允许",
    "不符合",
)
# 单门课"未知返回"连续出现的最大次数，超过则降级为 FAILED，避免死循环
MAX_UNKNOWN_STREAK = 20
# 后台任务允许的累计重登录上限（防止病态无限循环的安全阈值）
MAX_TOTAL_RELOGINS = 50
# 事件队列上限（仅保留最近的事件）
MAX_EVENTS = 200


# ====================================================================
# 进度与事件跟踪（线程安全）
# ====================================================================
_progress_lock = threading.Lock()
_progress = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "courses": {},  # id -> {"id","name","type","status","attempts","message"}
    "events": [],  # [{"ts","level","message"}]
}


def _reset_progress(courses) -> None:
    """初始化进度状态（后台任务开始时调用）。"""
    with _progress_lock:
        _progress["running"] = True
        _progress["started_at"] = datetime.now().isoformat(timespec="seconds")
        _progress["finished_at"] = None
        _progress["courses"] = {
            course.id: {
                "id": course.id,
                "name": course.name,
                "type": course.type,
                "status": database.STATUS_IN_PROGRESS,
                "attempts": 0,
                "message": "等待抢课",
            }
            for course in courses
        }
        _progress["events"] = []


def _set_progress_finished() -> None:
    with _progress_lock:
        _progress["running"] = False
        _progress["finished_at"] = datetime.now().isoformat(timespec="seconds")


def _update_course_progress(course_id, *, increment_attempts=False, **fields) -> None:
    with _progress_lock:
        entry = _progress["courses"].get(course_id)
        if entry is None:
            return
        if increment_attempts:
            entry["attempts"] = entry.get("attempts", 0) + 1
        entry.update(fields)


def _add_event(level: str, message: str) -> None:
    """记录一条事件并打印到终端。level: info/success/warn/error。"""
    with _progress_lock:
        _progress["events"].append(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "level": level,
                "message": message,
            }
        )
        if len(_progress["events"]) > MAX_EVENTS:
            del _progress["events"][:-MAX_EVENTS]
    log_method = logger.error if level == "error" else logger.info
    log_method("Enrollment event [%s]: %s", level, message)


def get_enroll_progress() -> dict:
    """返回当前抢课进度的快照（供 API 轮询）。"""
    with _progress_lock:
        courses = [copy.deepcopy(entry) for entry in _progress["courses"].values()]
        events = copy.deepcopy(_progress["events"])
        snapshot = {
            "running": _progress["running"],
            "started_at": _progress["started_at"],
            "finished_at": _progress["finished_at"],
            "courses": courses,
            "events": events,
        }
    total = len(courses)
    success = sum(1 for c in courses if c["status"] == database.STATUS_SUCCESS)
    failed = sum(1 for c in courses if c["status"] == database.STATUS_FAILED)
    snapshot["counts"] = {
        "total": total,
        "success": success,
        "failed": failed,
        "active": total - success - failed,
    }
    return snapshot


# ====================================================================
# 任务占用管理
# ====================================================================
def reserve_enroll_task() -> bool:
    """Atomically reserve the single enrollment worker slot."""
    global _task_running
    with _task_state_lock:
        if _task_running:
            return False
        _task_running = True
        return True


def is_enroll_task_running() -> bool:
    with _task_state_lock:
        return _task_running


def _release_enroll_task() -> None:
    global _task_running
    with _task_state_lock:
        _task_running = False


def _response_code(response) -> str | None:
    try:
        payload = response.json()
        return str(payload.get("code")) if isinstance(payload, dict) else None
    except (ValueError, AttributeError):
        return None


def _classify_response(response) -> str:
    """把学校返回结果归类为一个动作标签。

    返回：'success' | 'retry' | 'terminal' | 'expired' | 'unknown'
    """
    text = getattr(response, "text", "") or ""
    code = _response_code(response)
    status_code = getattr(response, "status_code", None)

    if is_session_expired_response(
        status_code=status_code,
        code=code,
        text=text,
    ):
        return "expired"
    if SUCCESS_KEYWORD in text:
        return "success"
    if CAPACITY_FULL_KEYWORD in text:
        return "retry"
    if any(keyword in text for keyword in TERMINAL_ERROR_KEYWORDS):
        return "terminal"
    return "unknown"


def _active_course_ids() -> set:
    """从数据库读取仍需抢课（未成功/未失败）的课程 id 集合。"""
    stored = {item["id"]: item for item in cart_service.get_courses_by_status("")}
    active = set()
    for course_id, row in stored.items():
        if row.get("status") not in (database.STATUS_SUCCESS, database.STATUS_FAILED):
            active.add(course_id)
    return active


def grab_courses(courses: list) -> bool:
    """
    执行多门课程的轮询抢课循环。

    对活动集中每门课程各发一次请求，根据学校返回结果分类：
        - 成功        → 标记 SUCCESS，发"已加入我的课程"事件，移出活动集
        - 容量已满    → 保持 ENROLLING，继续下一轮（一直抢）
        - 终态失败    → 标记 FAILED，移出活动集
        - 会话过期    → 立即返回 False，交由上层触发重登录
        - 未知返回    → 计入连续未知计数，超阈值降级为 FAILED

    参数：
        courses: 具有 id/type/name 属性的课程对象列表

    返回：
        True  - 所有课程都已到达终态（成功或失败），流程结束
        False - 会话过期，需要重登录后继续
    """
    active_ids = _active_course_ids()
    active = [course for course in courses if course.id in active_ids]
    unknown_streak = {course.id: 0 for course in active}

    if not active:
        return True

    for _ in range(config.count):
        if not active:
            break

        for course in list(active):
            try:
                # 发送选课请求（核心接口，不修改）
                response = choose_course.submit_course_selection(course.id, course.type)
                time.sleep(config.delay / 1000.0)
                _update_course_progress(course.id, increment_attempts=True)

                action = _classify_response(response)

                if action == "success":
                    cart_service.update_status(course.id, database.STATUS_SUCCESS)
                    _update_course_progress(
                        course.id,
                        status=database.STATUS_SUCCESS,
                        message="已抢到，已加入我的课程",
                    )
                    _add_event("success", f"{course.name} 已加入我的课程")
                    active.remove(course)

                elif action == "retry":
                    _update_course_progress(course.id, message="课容量已满，继续尝试")
                    unknown_streak[course.id] = 0

                elif action == "expired":
                    _add_event("warn", "检测到登录已过期，准备自动重新登录")
                    return False

                elif action == "terminal":
                    cart_service.update_status(course.id, database.STATUS_FAILED)
                    reason = (getattr(response, "text", "") or "").strip()[:60]
                    _update_course_progress(
                        course.id,
                        status=database.STATUS_FAILED,
                        message=reason or "该课程无法抢到",
                    )
                    _add_event("error", f"{course.name} 无法抢到：{reason or '学校返回终态错误'}")
                    active.remove(course)

                else:  # unknown
                    unknown_streak[course.id] += 1
                    snippet = (getattr(response, "text", "") or "").strip()[:60]
                    _update_course_progress(
                        course.id,
                        message=f"未知返回，继续尝试（{unknown_streak[course.id]}/{MAX_UNKNOWN_STREAK}）",
                    )
                    if unknown_streak[course.id] >= MAX_UNKNOWN_STREAK:
                        cart_service.update_status(course.id, database.STATUS_FAILED)
                        _update_course_progress(
                            course.id,
                            status=database.STATUS_FAILED,
                            message=f"连续多次未知返回，已停止：{snippet}",
                        )
                        _add_event(
                            "error",
                            f"{course.name} 连续 {MAX_UNKNOWN_STREAK} 次未知返回，已停止尝试",
                        )
                        active.remove(course)
                    else:
                        logger.warning(
                            "Unknown school response for %s: %s",
                            course.name,
                            snippet,
                        )

            except KeyboardInterrupt:
                logger.info("Enrollment worker interrupted")
                return True
            except Exception as exc:
                # 网络抖动等瞬时异常：保持课程活动，稍后重试
                logger.warning("Course request failed for %s: %s", course.name, exc)
                _update_course_progress(course.id, message=f"网络异常，重试中：{exc}")
                time.sleep(max(config.delay, 350) / 1000.0)
                continue

    return True


def relogin_and_continue(courses: list) -> bool:
    """自动重登录并继续抢课（供命令行兼容路径调用）。"""
    success, error = attempt_automatic_relogin(max_attempts=config.ocr_relogin_max_attempts)
    if success:
        logger.info("OCR automatic re-login succeeded")
        return grab_courses(courses)
    logger.warning("Automatic re-login failed: %s", error)
    return False


def _mark_unresolved_failed(courses: list) -> None:
    stored = {item["id"]: item for item in cart_service.get_courses_by_status("")}
    for course in courses:
        if stored.get(course.id, {}).get("status") == database.STATUS_IN_PROGRESS:
            cart_service.update_status(course.id, database.STATUS_FAILED)
            _update_course_progress(
                course.id,
                status=database.STATUS_FAILED,
                message="任务结束时仍未抢到",
            )


def run_enroll_task(reserved: bool = False):
    """
    后台抢课任务入口

    从数据库读取所有 PENDING 状态的课程，执行抢课循环。
    会话过期时自动重登录，仅在连续多次重登录失败后才停止。
    """
    if not reserved and not reserve_enroll_task():
        logger.warning("Ignored duplicate enrollment worker request")
        return

    try:
        _run_enroll_task()
    finally:
        _release_enroll_task()
        _set_progress_finished()


def _run_enroll_task():
    """Internal worker body; callers must hold the task reservation."""
    courses_data = cart_service.get_courses_by_status(database.STATUS_NOT_STARTED)

    courses = [
        EnrollmentCourse(id=item["id"], type=item["type"], name=item["name"])
        for item in courses_data
    ]

    if not courses:
        logger.info("No pending cart courses")
        return

    _reset_progress(courses)
    logger.info("Starting enrollment worker with %s course(s)", len(courses))
    for c in courses:
        cart_service.update_status(c.id, database.STATUS_IN_PROGRESS)
        logger.info("Enrollment queue item: %s", c.name)
    _add_event("info", f"开始抢课，共 {len(courses)} 门课程")

    consecutive_relogin_failures = 0
    total_relogins = 0
    max_failures = max(1, int(config.relogin_max_retries))

    try:
        while True:
            try:
                finished = grab_courses(courses)
            except KeyboardInterrupt:
                logger.info("Enrollment worker interrupted")
                break

            if finished:
                _add_event("info", "抢课流程结束")
                break

            # 会话过期：尝试自动重登录
            if total_relogins >= MAX_TOTAL_RELOGINS:
                _add_event("error", "累计重登录次数过多，已停止，请手动登录")
                break

            total_relogins += 1
            success, error = attempt_automatic_relogin(max_attempts=config.ocr_relogin_max_attempts)
            if success:
                consecutive_relogin_failures = 0
                _add_event("info", "会话过期，已自动重新登录，继续抢课")
                continue

            consecutive_relogin_failures += 1
            _add_event(
                "warn",
                f"自动重登录失败（{consecutive_relogin_failures}/{max_failures}）：{error}",
            )
            if consecutive_relogin_failures >= max_failures:
                _add_event("error", "连续多次自动重登录失败，已停止，请返回登录页手动登录")
                break
            time.sleep(min(2.0 * consecutive_relogin_failures, 8.0))
    finally:
        _mark_unresolved_failed(courses)
        snapshot = get_enroll_progress()
        counts = snapshot["counts"]
        _add_event(
            "info",
            f"任务收尾：成功 {counts['success']} 门，失败 {counts['failed']} 门",
        )
