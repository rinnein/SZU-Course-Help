"""Authentication, in-memory session state, and OCR session recovery."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any

import config
import logic
from school_password import encrypt_school_password

logger = logging.getLogger(__name__)
LOGIN_ERROR_MSG = "登录失败，请检查学号、密码、卡密或验证码是否正确"

_state_lock = threading.RLock()
_automatic_relogin_lock = threading.Lock()
_session_generation = 0
_relogin_state: dict[str, str | int] = {
    "status": "idle",
    "message": "",
    "started_at": "",
    "finished_at": "",
    "max_attempts": 0,
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _reset_relogin_state_locked() -> None:
    _relogin_state.update(
        {
            "status": "idle",
            "message": "",
            "started_at": "",
            "finished_at": "",
            "max_attempts": 0,
        }
    )


def _set_relogin_state(
    status: str,
    message: str,
    *,
    max_attempts: int | None = None,
) -> None:
    with _state_lock:
        if status == "running":
            _relogin_state["started_at"] = _now_iso()
            _relogin_state["finished_at"] = ""
        elif status in {"success", "failed"}:
            _relogin_state["finished_at"] = _now_iso()
        _relogin_state["status"] = status
        _relogin_state["message"] = str(message or "")
        if max_attempts is not None:
            _relogin_state["max_attempts"] = max(1, int(max_attempts))


def _newer_session_result_locked(
    expected_generation: int,
) -> tuple[bool, str] | None:
    """Prefer session changes made while one OCR recovery was in flight."""
    if _session_generation == expected_generation:
        return None
    if config.token and config.combined_cookie:
        logger.info("Reused a newer school session instead of an OCR result")
        _set_relogin_state("success", "检测到新的登录已完成，正在使用最新学校会话")
        return True, ""
    logger.info("Discarded an OCR result because the login state changed")
    return False, "登录状态已变化，已放弃过期的自动重登录结果"


def _finish_relogin_failure(
    expected_generation: int,
    error: str,
) -> tuple[bool, str]:
    """Invalidate only the session owned by this recovery attempt."""
    normalized_error = str(error or "自动重新登录失败")
    with _state_lock:
        newer_result = _newer_session_result_locked(expected_generation)
        if newer_result is not None:
            return newer_result
        invalidate_school_session()
        _set_relogin_state("failed", normalized_error)
    return False, normalized_error


def validate_login_params(
    student_id: str,
    password: str,
    card_key: str,
    verify_code: list,
    vtoken: str = "",
    cookie: str = "",
) -> str | None:
    """Validate the complete school-login input without revealing which part failed."""
    if not student_id or not re.fullmatch(r"\d{6,12}", student_id.strip()):
        return LOGIN_ERROR_MSG
    if not password or not password.strip():
        return LOGIN_ERROR_MSG
    if not card_key or not card_key.strip() or len(card_key.strip()) > 2048:
        return LOGIN_ERROR_MSG
    if not vtoken or not vtoken.strip() or not cookie or not cookie.strip():
        return LOGIN_ERROR_MSG
    if not verify_code or len(verify_code) != 4:
        return LOGIN_ERROR_MSG
    for coordinate in verify_code:
        if (
            not isinstance(coordinate, (list, tuple))
            or len(coordinate) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in coordinate)
            or not (0 <= coordinate[0] <= 250 and 0 <= coordinate[1] <= 80)
        ):
            return LOGIN_ERROR_MSG
    return None


def encrypt_password(password: str) -> str:
    """Return the school's legacy ``loginPwd`` value."""
    return encrypt_school_password(password)


def perform_school_login(
    student_id: str,
    vtoken: str,
    login_pwd: str,
    centres_string: str,
    parsed_cookie: str,
) -> dict[str, Any]:
    """Call the school login endpoint without changing its request fields."""
    return logic.login(
        student_id,
        vtoken,
        login_pwd,
        centres_string,
        parsed_cookie,
    )


def _advance_session_generation() -> None:
    global _session_generation
    _session_generation += 1


def save_login_state(
    login_cookie: str,
    captcha_cookie: str,
    student_id: str,
    password: str,
    token: str,
    *,
    preserve_relogin_state: bool = False,
) -> None:
    """Atomically store the local credentials needed for session recovery."""
    if not login_cookie or not captcha_cookie or not token:
        raise ValueError("complete login cookies and token are required")
    with _state_lock:
        config.combined_cookie = f"{login_cookie}; {captcha_cookie}"
        config.token = str(token)
        config.student_id = str(student_id)
        config.password = password
        config.elective_batch_code = ""
        config.elective_batch_name = ""
        _advance_session_generation()
        if not preserve_relogin_state:
            _reset_relogin_state_locked()


def clear_login_state() -> None:
    """Clear credentials and all school-session state."""
    with _state_lock:
        config.combined_cookie = ""
        config.token = ""
        config.student_id = ""
        config.password = ""
        config.elective_batch_code = ""
        config.elective_batch_name = ""
        _advance_session_generation()
        _reset_relogin_state_locked()


def invalidate_school_session() -> None:
    """Drop expired school tokens while retaining credentials for recovery."""
    with _state_lock:
        config.combined_cookie = ""
        config.token = ""
        config.elective_batch_code = ""
        config.elective_batch_name = ""
        _advance_session_generation()


def clear_elective_batch() -> None:
    """Clear a stale batch while preserving the valid school login session."""
    with _state_lock:
        config.elective_batch_code = ""
        config.elective_batch_name = ""


def get_session_snapshot() -> dict[str, str | bool | int]:
    """Return a consistent, password-free view of the current session."""
    with _state_lock:
        return {
            "logged_in": bool(config.token and config.combined_cookie),
            "student_id": str(config.student_id or ""),
            "batch_code": str(config.elective_batch_code or ""),
            "batch_name": str(config.elective_batch_name or ""),
            "relogin_in_progress": _relogin_state["status"] == "running",
            "relogin_status": str(_relogin_state["status"]),
            "relogin_message": str(_relogin_state["message"]),
            "relogin_started_at": str(_relogin_state["started_at"]),
            "relogin_finished_at": str(_relogin_state["finished_at"]),
            "relogin_max_attempts": int(_relogin_state["max_attempts"]),
        }


def refresh_elective_batch(student_id: str, token: str) -> str:
    """Refresh the batch only if the originating session is still current."""
    normalized_student_id = str(student_id)
    normalized_token = str(token)
    with _state_lock:
        if (
            config.student_id != normalized_student_id
            or config.token != normalized_token
            or not config.combined_cookie
        ):
            raise RuntimeError("登录状态已变化，已放弃批次刷新")
        combined_cookie = str(config.combined_cookie)

    batch_code, batch_name = logic.fetch_elective_batch(
        normalized_student_id,
        normalized_token,
        combined_cookie,
    )
    with _state_lock:
        if (
            config.student_id != normalized_student_id
            or config.token != normalized_token
            or config.combined_cookie != combined_cookie
        ):
            raise RuntimeError("登录状态已变化，已丢弃过期批次结果")
        config.elective_batch_code = batch_code
        config.elective_batch_name = batch_name
    return batch_name


def attempt_ocr_relogin(
    max_attempts: int = config.ocr_relogin_max_attempts,
) -> tuple[str, str, str, str]:
    """Solve a fresh captcha using the credentials retained in memory."""
    with _state_lock:
        if not config.student_id or not config.password:
            raise RuntimeError("没有可用于自动重登录的内存凭据")
    return logic.verify_vcode(max_attempts=max_attempts)


def attempt_automatic_relogin(
    max_attempts: int = config.ocr_relogin_max_attempts,
) -> tuple[bool, str]:
    """Serialize OCR recovery and reuse a session restored by another caller."""
    with _state_lock:
        observed_generation = _session_generation

    with _automatic_relogin_lock:
        with _state_lock:
            if (
                _session_generation != observed_generation
                and config.token
                and config.combined_cookie
            ):
                logger.info("Reused a school session restored by another request")
                return True, ""
            student_id = str(config.student_id)
            password = config.password
            owned_generation = _session_generation

        _set_relogin_state(
            "running",
            f"正在使用 OCR 自动重新登录，最多识别 {max_attempts} 张验证码",
            max_attempts=max_attempts,
        )
        try:
            vtoken, captcha_cookie, login_pwd, centres_string = attempt_ocr_relogin(
                max_attempts=max_attempts
            )
            login_result = perform_school_login(
                student_id,
                vtoken,
                login_pwd,
                centres_string,
                captcha_cookie,
            )
            if not login_result.get("success"):
                error = login_result.get("error_msg") or "学校拒绝自动重登录"
                return _finish_relogin_failure(owned_generation, str(error))

            with _state_lock:
                newer_result = _newer_session_result_locked(owned_generation)
                if newer_result is not None:
                    return newer_result
                save_login_state(
                    str(login_result["cookie"]),
                    captcha_cookie,
                    student_id,
                    password,
                    str(login_result["token"]),
                    preserve_relogin_state=True,
                )
                owned_generation = _session_generation
                restored_token = str(config.token)

            try:
                refresh_elective_batch(student_id, restored_token)
            except logic.SchoolBatchSessionExpiredError as exc:
                return _finish_relogin_failure(owned_generation, str(exc))
            except Exception as exc:
                with _state_lock:
                    newer_result = _newer_session_result_locked(owned_generation)
                    if newer_result is not None:
                        return newer_result
                    logger.warning(
                        "Automatic re-login succeeded but batch refresh failed: %s",
                        exc,
                    )
                    _set_relogin_state(
                        "success",
                        "自动重新登录成功，选课批次暂未刷新；抢课任务将继续",
                    )
                    return True, ""
            with _state_lock:
                newer_result = _newer_session_result_locked(owned_generation)
                if newer_result is not None:
                    return newer_result
                _set_relogin_state("success", "自动重新登录成功，学校会话已恢复")
                return True, ""
        except (ImportError, ModuleNotFoundError) as exc:
            logger.warning("OCR dependency unavailable: %s", exc)
            error = f"OCR 依赖不可用: {exc}"
            return _finish_relogin_failure(owned_generation, error)
        except Exception as exc:
            logger.exception("Automatic school re-login failed")
            error = str(exc) or type(exc).__name__
            return _finish_relogin_failure(owned_generation, error)


__all__ = [
    "LOGIN_ERROR_MSG",
    "attempt_automatic_relogin",
    "attempt_ocr_relogin",
    "clear_elective_batch",
    "clear_login_state",
    "encrypt_password",
    "get_session_snapshot",
    "invalidate_school_session",
    "perform_school_login",
    "refresh_elective_batch",
    "save_login_state",
    "validate_login_params",
]
