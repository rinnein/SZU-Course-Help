"""
抢课服务模块

职责：
    1. 执行抢课循环（遍历购物车课程，调用学校选课接口）
    2. 对学校返回结果分类处理（成功 / 容量满重试 / 终态失败 / 会话过期）
    3. 多门课程轮询抢课：抢到的立即停止，未抢到的继续
    4. 会话过期自动重登录，连续多次失败后暂停并保留队列
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
from enum import StrEnum

import requests

import choose_course
import config
import database
from school_session import is_session_expired_response
from services import cart_service
from services.auth_service import (
    attempt_automatic_relogin,
)

_task_state_lock = threading.RLock()
_task_condition = threading.Condition(_task_state_lock)
_task_state = {
    "running": False,
    "paused": False,
    "pause_acknowledged": False,
    "pause_reason": "",
    "pause_source": "",
    "paused_at": "",
    "stop_requested": False,
    "stop_reason": "",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrollmentCourse:
    """Minimal immutable course data used by the background worker."""

    id: str
    type: str
    name: str


class GrabOutcome(StrEnum):
    """Why one enrollment pass returned control to the worker."""

    COMPLETED = "completed"
    CONTINUE = "continue"
    SESSION_EXPIRED = "session_expired"
    PAUSED = "paused"


# ====================================================================
# 学校返回结果分类关键词
# ====================================================================
# 抢到成功
SUCCESS_KEYWORD = "添加选课志愿成功"
# 容量已满：属于可重试情形，继续下一轮，不受未知返回阈值限制
CAPACITY_FULL_KEYWORDS = (
    "该课程超过课容量",
    "超过课容量",
    "课容量已满",
    "课程容量已满",
    "超过课程容量",
    "选课人数已满",
    "课程人数已满",
    "教学班容量已满",
    "该教学班已满",
)
# 学校批次存在，但此刻不在实际开放时段。此类结果只能暂停，不能永久失败。
WINDOW_CLOSED_KEYWORDS = (
    "当前时间不在选课开放时间范围内",
    "不在选课开放时间",
    "未在选课开放时间",
    "不在开放时间范围内",
    "不在选课时间",
    "不在补选时间",
    "当前时间不在补选",
    "非选课时间",
    "未到选课时间",
    "选课时间未到",
    "选课时间已过",
    "选课尚未开始",
    "选课未开始",
    "选课暂未开放",
    "选课已结束",
    "选课已经结束",
)
# 学校临时繁忙：继续轮询，但不计入未知返回。
RETRYABLE_ERROR_KEYWORDS = (
    "系统繁忙",
    "服务繁忙",
    "请稍后再试",
    "请求频繁",
    "操作频繁",
    "网络繁忙",
)
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
    "不在补选",
    "超过学分",
    "学分已满",
    "学分已达上限",
    "学分已达到上限",
    "选课门数已达上限",
    "选课门数已达到上限",
    "志愿数已达上限",
    "志愿数已达到上限",
    "不满足",
    "无权限",
    "不允许",
    "不符合",
)
# 连续未知或网络异常达到阈值时触发保护性暂停，不会把课程写成 FAILED。
MAX_UNKNOWN_STREAK = 5
MAX_NETWORK_STREAK = 8
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


def _remove_course_progress(course_id: str) -> bool:
    """Remove a deleted cart item from the task snapshot shown in the UI."""
    with _progress_lock:
        return _progress["courses"].pop(course_id, None) is not None


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
    snapshot.update(get_enroll_task_state())
    return snapshot


# ====================================================================
# 任务占用管理
# ====================================================================
def reserve_enroll_task() -> bool:
    """Atomically reserve the single enrollment worker slot."""
    with _task_condition:
        if _task_state["running"]:
            return False
        _task_state.update(
            {
                "running": True,
                "paused": False,
                "pause_acknowledged": False,
                "pause_reason": "",
                "pause_source": "",
                "paused_at": "",
                "stop_requested": False,
                "stop_reason": "",
            }
        )
        return True


def start_enroll_worker() -> bool:
    """Reserve and launch the long-running worker outside the HTTP request lifecycle."""
    if not reserve_enroll_task():
        return False
    worker = threading.Thread(
        target=run_enroll_task,
        args=(True,),
        name="course-enrollment-worker",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        _release_enroll_task()
        raise
    return True


def is_enroll_task_running() -> bool:
    with _task_state_lock:
        return bool(_task_state["running"])


def get_enroll_task_state() -> dict[str, str | bool]:
    """Return a thread-safe, JSON-ready snapshot of task controls."""
    with _task_state_lock:
        return {
            "running": bool(_task_state["running"]),
            "paused": bool(_task_state["paused"]),
            "pause_acknowledged": bool(_task_state["pause_acknowledged"]),
            "pause_reason": str(_task_state["pause_reason"]),
            "pause_source": str(_task_state["pause_source"]),
            "paused_at": str(_task_state["paused_at"]),
            "stopping": bool(_task_state["stop_requested"]),
            "stopping_reason": str(_task_state["stop_reason"]),
        }


def pause_enroll_task(
    reason: str = "用户已暂停抢课任务",
    *,
    source: str = "user",
) -> tuple[bool, str]:
    """Pause before the next school request while retaining the active queue."""
    normalized_reason = str(reason or "抢课任务已暂停").strip()
    changed = False
    with _task_condition:
        if not _task_state["running"]:
            return False, "当前没有正在运行的抢课任务"
        if _task_state["stop_requested"]:
            return False, "抢课任务正在结束，请稍候"
        if not _task_state["paused"]:
            changed = True
            _task_state.update(
                {
                    "paused": True,
                    "pause_acknowledged": False,
                    "pause_reason": normalized_reason,
                    "pause_source": str(source or "user"),
                    "paused_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
        _task_condition.notify_all()

    if changed:
        level = "info" if source == "user" else "warn"
        _add_event(level, normalized_reason)
    return True, normalized_reason


def resume_enroll_task() -> tuple[bool, str]:
    """Resume a paused task without resetting courses or attempt counters."""
    with _task_condition:
        if not _task_state["running"]:
            return False, "当前没有可继续的抢课任务"
        if _task_state["stop_requested"]:
            return False, "待处理课程已清空，抢课任务正在结束"
        if not _task_state["paused"]:
            return True, "抢课任务已经在运行"
        _task_state.update(
            {
                "paused": False,
                "pause_acknowledged": False,
                "pause_reason": "",
                "pause_source": "",
                "paused_at": "",
            }
        )
        _task_condition.notify_all()

    _add_event("info", "抢课任务已继续")
    return True, "抢课任务已继续"


def _wait_until_resumed() -> bool:
    """Block at a request boundary and acknowledge that cart edits are safe."""
    with _task_condition:
        if _task_state["running"] and _task_state["paused"] and not _task_state["stop_requested"]:
            _task_state["pause_acknowledged"] = True
            _task_condition.notify_all()
        while (
            _task_state["running"] and _task_state["paused"] and not _task_state["stop_requested"]
        ):
            _task_condition.wait(timeout=1.0)
        _task_state["pause_acknowledged"] = False
        return bool(_task_state["running"] and not _task_state["stop_requested"])


def _wait_between_requests(seconds: float) -> bool:
    """Use an interruptible delay so a pause takes effect promptly."""
    standalone = False
    with _task_condition:
        if _task_state["paused"] or _task_state["stop_requested"]:
            return False
        if not _task_state["running"]:
            standalone = True
        else:
            _task_condition.wait(timeout=max(0.0, float(seconds)))
            return bool(
                _task_state["running"]
                and not _task_state["paused"]
                and not _task_state["stop_requested"]
            )
    if standalone:
        time.sleep(max(0.0, float(seconds)))
    return True


def _release_enroll_task() -> None:
    with _task_condition:
        _task_state.update(
            {
                "running": False,
                "paused": False,
                "pause_acknowledged": False,
                "pause_reason": "",
                "pause_source": "",
                "paused_at": "",
                "stop_requested": False,
                "stop_reason": "",
            }
        )
        _task_condition.notify_all()


def _response_payload(response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except (ValueError, AttributeError):
        return {}


def _response_code(response) -> str | None:
    payload = _response_payload(response)
    code = payload.get("code")
    return str(code) if code is not None else None


def _response_message(response) -> str:
    payload = _response_payload(response)
    for key in ("msg", "message", "error_msg"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (getattr(response, "text", "") or "").strip()


def _classify_response(response) -> str:
    """把学校返回结果归类为一个动作标签。

    返回：success | retry | terminal | expired | window_closed | unknown
    """
    text = getattr(response, "text", "") or ""
    message = _response_message(response)
    searchable = f"{message}\n{text}"
    code = _response_code(response)
    status_code = getattr(response, "status_code", None)

    if is_session_expired_response(
        status_code=status_code,
        code=code,
        text=text,
    ):
        return "expired"
    if SUCCESS_KEYWORD in searchable:
        return "success"
    if any(keyword in searchable for keyword in CAPACITY_FULL_KEYWORDS):
        return "retry"
    if any(keyword in searchable for keyword in WINDOW_CLOSED_KEYWORDS):
        return "window_closed"
    if any(keyword in searchable for keyword in RETRYABLE_ERROR_KEYWORDS):
        return "retry"
    if any(keyword in searchable for keyword in TERMINAL_ERROR_KEYWORDS):
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


def remove_cart_course(course_id: str) -> dict[str, bool | str]:
    """Safely remove a cart item, including while a worker is fully paused.

    A pause request is not enough on its own: the worker may still have one school
    request in flight.  Deletion is allowed only after ``_wait_until_resumed`` has
    acknowledged the pause at a request boundary.
    """
    normalized_id = str(course_id or "").strip()
    if not normalized_id:
        return {
            "success": False,
            "message": "课程 ID 不能为空",
            "error_code": "INVALID_COURSE_ID",
        }

    should_stop = False
    was_running = False
    with _task_condition:
        stored_course = next(
            (
                item
                for item in cart_service.get_courses_by_status("")
                if item.get("id") == normalized_id
            ),
            None,
        )
        if stored_course is None:
            return {
                "success": False,
                "message": "删除失败或课程不存在",
                "error_code": "COURSE_NOT_FOUND",
            }
        is_terminal = stored_course.get("status") in (
            database.STATUS_SUCCESS,
            database.STATUS_FAILED,
        )
        was_running = bool(_task_state["running"])
        if was_running and _task_state["stop_requested"]:
            return {
                "success": False,
                "message": "抢课任务正在结束，请稍候后再修改清单",
                "error_code": "ENROLL_TASK_STOPPING",
            }
        if was_running and not is_terminal:
            if not _task_state["paused"]:
                return {
                    "success": False,
                    "message": "请先暂停抢课任务，再移除课程",
                    "error_code": "ENROLL_TASK_NOT_PAUSED",
                }
            if not _task_state["pause_acknowledged"]:
                return {
                    "success": False,
                    "message": "正在完成当前学校请求，安全暂停后即可移除",
                    "error_code": "ENROLL_TASK_PAUSE_PENDING",
                }

        result = cart_service.delete_course(normalized_id)
        if not result["success"]:
            return {
                **result,
                "error_code": "COURSE_NOT_FOUND",
            }

        if was_running and not _active_course_ids():
            should_stop = True
            stop_reason = "待处理课程已全部移除，抢课任务正在结束"
            _task_state.update(
                {
                    "paused": False,
                    "pause_acknowledged": False,
                    "pause_reason": "",
                    "pause_source": "",
                    "paused_at": "",
                    "stop_requested": True,
                    "stop_reason": stop_reason,
                }
            )
            _task_condition.notify_all()

    removed_from_progress = _remove_course_progress(normalized_id)
    if was_running or removed_from_progress:
        _add_event("info", "课程已从本地清单和本轮任务中移除")

    if should_stop:
        return {
            "success": True,
            "message": "课程已移除；清单中已无待处理课程，任务正在结束",
            "task_stopping": True,
        }
    return {
        "success": True,
        "message": "课程已从本地清单移除",
        "task_stopping": False,
    }


def grab_courses(courses: list) -> GrabOutcome:
    """
    执行多门课程的轮询抢课循环。

    对活动集中每门课程各发一次请求，根据学校返回结果分类：
        - 成功        → 标记 SUCCESS，发"已加入我的课程"事件，移出活动集
        - 容量已满    → 保持 ENROLLING，继续下一轮（一直抢）
        - 终态失败    → 标记 FAILED，移出活动集
        - 未到开放时间 → 立即自动暂停，等待用户继续
        - 会话过期    → 交由上层触发 OCR 自动重登录
        - 未知返回    → 计入连续未知计数，超阈值保护性暂停

    参数：
        courses: 具有 id/type/name 属性的课程对象列表

    返回：
        GrabOutcome，明确区分完成、继续、重登录和暂停。
    """
    active_ids = _active_course_ids()
    active = [course for course in courses if course.id in active_ids]
    unknown_streak = {course.id: 0 for course in active}
    network_streak = {course.id: 0 for course in active}

    if not active:
        return GrabOutcome.COMPLETED

    max_rounds = max(1, int(config.count))
    for _ in range(max_rounds):
        if not active:
            break

        for course in list(active):
            if get_enroll_task_state()["paused"]:
                return GrabOutcome.PAUSED
            try:
                # 发送选课请求（核心接口，不修改）
                _update_course_progress(course.id, increment_attempts=True)
                response = choose_course.submit_course_selection(course.id, course.type)
                network_streak[course.id] = 0
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
                    reason = _response_message(response)
                    retry_message = (
                        "课容量已满，继续尝试"
                        if any(keyword in reason for keyword in CAPACITY_FULL_KEYWORDS)
                        else f"学校暂时未受理，继续尝试：{reason[:80] or '请稍后再试'}"
                    )
                    _update_course_progress(course.id, message=retry_message)
                    unknown_streak[course.id] = 0

                elif action == "expired":
                    _add_event("warn", "检测到登录已过期，准备自动重新登录")
                    return GrabOutcome.SESSION_EXPIRED

                elif action == "window_closed":
                    reason = _response_message(response)[:160] or "学校当前未开放选课"
                    message = f"学校提示“{reason}”，任务已自动暂停；开放后可点击继续"
                    _update_course_progress(course.id, message=message)
                    pause_enroll_task(message, source="school_window")
                    return GrabOutcome.PAUSED

                elif action == "terminal":
                    cart_service.update_status(course.id, database.STATUS_FAILED)
                    reason = _response_message(response)[:160]
                    _update_course_progress(
                        course.id,
                        status=database.STATUS_FAILED,
                        message=reason or "该课程无法抢到",
                    )
                    _add_event("error", f"{course.name} 无法抢到：{reason or '学校返回终态错误'}")
                    active.remove(course)

                else:  # unknown
                    unknown_streak[course.id] += 1
                    snippet = _response_message(response)[:160]
                    _update_course_progress(
                        course.id,
                        message=(
                            "学校返回暂时无法识别，准备保护性暂停"
                            if unknown_streak[course.id] >= MAX_UNKNOWN_STREAK
                            else (
                                "学校返回暂时无法识别，继续观察"
                                f"（{unknown_streak[course.id]}/{MAX_UNKNOWN_STREAK}）"
                            )
                        ),
                    )
                    if unknown_streak[course.id] >= MAX_UNKNOWN_STREAK:
                        reason = (
                            f"{course.name} 连续 {MAX_UNKNOWN_STREAK} 次收到无法识别的学校返回，"
                            f"任务已保护性暂停：{snippet or '响应内容为空'}"
                        )
                        _update_course_progress(course.id, message=reason)
                        pause_enroll_task(reason, source="unknown_response")
                        return GrabOutcome.PAUSED
                    else:
                        logger.warning(
                            "Unknown school response for %s: %s",
                            course.name,
                            snippet,
                        )

                if active and not _wait_between_requests(config.delay / 1000.0):
                    return GrabOutcome.PAUSED

            except choose_course.SchoolSessionExpiredError:
                _add_event("warn", "检测到登录已过期，准备自动重新登录")
                return GrabOutcome.SESSION_EXPIRED
            except KeyboardInterrupt:
                logger.info("Enrollment worker interrupted")
                return GrabOutcome.COMPLETED
            except requests.RequestException as exc:
                # 网络抖动等瞬时异常：保持课程活动；连续失败时暂停，避免无休止请求。
                unknown_streak[course.id] = 0
                network_streak[course.id] += 1
                logger.warning("Course request failed for %s: %s", course.name, exc)
                message = (
                    f"学校请求异常（{network_streak[course.id]}/{MAX_NETWORK_STREAK}）："
                    f"{str(exc)[:100] or type(exc).__name__}"
                )
                _update_course_progress(course.id, message=message)
                if network_streak[course.id] >= MAX_NETWORK_STREAK:
                    reason = f"{course.name} 连续请求学校失败，任务已保护性暂停"
                    _update_course_progress(course.id, message=reason)
                    pause_enroll_task(reason, source="network_error")
                    return GrabOutcome.PAUSED
                if not _wait_between_requests(max(config.delay, 350) / 1000.0):
                    return GrabOutcome.PAUSED
                continue
            except Exception as exc:
                logger.exception("Enrollment worker failed for %s", course.name)
                reason = f"{course.name} 抢课任务发生内部异常，已保护性暂停：{type(exc).__name__}"
                _update_course_progress(course.id, message=reason)
                pause_enroll_task(reason, source="internal_error")
                return GrabOutcome.PAUSED

    return GrabOutcome.COMPLETED if not active else GrabOutcome.CONTINUE


def relogin_and_continue(courses: list) -> bool:
    """自动重登录并继续抢课（供命令行兼容路径调用）。"""
    success, error = attempt_automatic_relogin(max_attempts=config.ocr_relogin_max_attempts)
    if success:
        logger.info("OCR automatic re-login succeeded")
        return grab_courses(courses) == GrabOutcome.COMPLETED
    logger.warning("Automatic re-login failed: %s", error)
    return False


def _return_unresolved_to_pending(courses: list) -> None:
    """Keep non-terminal courses retryable if the worker exits unexpectedly."""
    stored = {item["id"]: item for item in cart_service.get_courses_by_status("")}
    for course in courses:
        if stored.get(course.id, {}).get("status") == database.STATUS_IN_PROGRESS:
            cart_service.update_status(course.id, database.STATUS_NOT_STARTED)
            _update_course_progress(
                course.id,
                status=database.STATUS_NOT_STARTED,
                message="任务已结束，课程已保留，可重新启动",
            )


def run_enroll_task(reserved: bool = False):
    """
    后台抢课任务入口

    从数据库读取所有 PENDING 状态的课程，执行抢课循环。
    会话过期时自动重登录，连续多次失败后暂停并保留队列。
    """
    if reserved:
        if not is_enroll_task_running() and not reserve_enroll_task():
            logger.warning("Could not restore the reserved enrollment worker slot")
            return
    elif not reserve_enroll_task():
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
    max_failures = max(1, int(config.relogin_max_retries))

    try:
        while True:
            if get_enroll_task_state()["paused"]:
                if not _wait_until_resumed():
                    break
                continue
            try:
                outcome = grab_courses(courses)
            except KeyboardInterrupt:
                logger.info("Enrollment worker interrupted")
                break

            if outcome == GrabOutcome.COMPLETED:
                _add_event("info", "抢课流程结束")
                break
            if outcome == GrabOutcome.CONTINUE:
                continue
            if outcome == GrabOutcome.PAUSED:
                if not _wait_until_resumed():
                    break
                continue

            # 会话过期：尝试自动重登录
            _add_event(
                "warn",
                f"学校会话已过期，正在 OCR 自动重新登录（最多 {config.ocr_relogin_max_attempts} 次）",
            )
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
                reason = "连续多次自动重登录失败，任务已暂停；请手动登录后点击继续"
                for course in courses:
                    _update_course_progress(course.id, message=reason)
                pause_enroll_task(reason, source="relogin_failed")
                if not _wait_until_resumed():
                    break
                consecutive_relogin_failures = 0
                continue
            if (
                not _wait_between_requests(min(2.0 * consecutive_relogin_failures, 8.0))
                and not _wait_until_resumed()
            ):
                break
    finally:
        _return_unresolved_to_pending(courses)
        snapshot = get_enroll_progress()
        counts = snapshot["counts"]
        _add_event(
            "info",
            (
                f"任务收尾：成功 {counts['success']} 门，失败 {counts['failed']} 门，"
                f"保留 {counts['active']} 门"
            ),
        )
