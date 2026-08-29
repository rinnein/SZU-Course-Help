"""Authentication, in-memory session state, and OCR session recovery."""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import datetime
from typing import Any

import config
import logic
from campus import (
    DEFAULT_CAMPUS_CODE,
    DEFAULT_CAMPUS_NAME,
    campus_name,
    get_campus,
)
from school_password import encrypt_school_password
from services import backend_service
from services.session_store import SessionStoreError
from services.session_store import clear as clear_persisted_session
from services.session_store import load as load_persisted_session
from services.session_store import save as save_persisted_session

logger = logging.getLogger(__name__)
LOGIN_ERROR_MSG = "登录失败，请检查学号、密码、卡密或验证码是否正确"

_state_lock = threading.RLock()
_automatic_relogin_lock = threading.Lock()
_automatic_relogin_worker_lock = threading.Lock()
_automatic_relogin_worker: threading.Thread | None = None
_session_generation = 0
_relogin_state: dict[str, str | int | float] = {
    "status": "idle",
    "message": "",
    "started_at": "",
    "finished_at": "",
    "max_attempts": 0,
    "failure_count": 0,
    "next_retry_at": 0.0,
}
_restored_session_pending_validation = False


def _persist_current_session() -> None:
    """Persist recoverable session state without breaking active requests."""
    if not config.token or not config.combined_cookie or not config.student_id:
        try:
            clear_persisted_session()
        except OSError as exc:
            logger.warning("Unable to clear persisted school session: %s", exc)
        return
    try:
        save_persisted_session(
            {
                "student_id": str(config.student_id or ""),
                "password": str(config.password or ""),
                "token": str(config.token or ""),
                "combined_cookie": str(config.combined_cookie or ""),
                "webvpn_cookie": str(config.webvpn_cookie or ""),
                "authserver_cookie": str(config.authserver_cookie or ""),
                "backend_preference": backend_service.get_preference(),
                "active_backend": str(config.active_backend or config.BACKEND_PRIMARY),
                "batch_code": str(config.elective_batch_code or ""),
                "batch_name": str(config.elective_batch_name or ""),
            }
        )
    except SessionStoreError as exc:
        logger.warning("Unable to persist local school session: %s", exc)


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
            "failure_count": 0,
            "next_retry_at": 0.0,
        }
    )


def _reset_relogin_attempts_locked() -> None:
    """Clear the failure budget after a manual or automatic login succeeds."""
    _relogin_state["failure_count"] = 0
    _relogin_state["next_retry_at"] = 0.0


def _relogin_block_reason_locked() -> str | None:
    """Return a cooldown/exhaustion message without starting OCR."""
    max_retries = max(1, int(config.relogin_max_retries))
    failure_count = int(_relogin_state["failure_count"])
    if failure_count >= max_retries:
        return f"自动重登录已连续失败 {max_retries} 次，已停止后台尝试，请手动登录"
    remaining = float(_relogin_state["next_retry_at"]) - time.monotonic()
    if remaining > 0:
        return f"自动重登录上次失败，请在 {math.ceil(remaining)} 秒后重试"
    return None


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
        _reset_relogin_attempts_locked()
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
        failure_count = int(_relogin_state["failure_count"]) + 1
        max_retries = max(1, int(config.relogin_max_retries))
        if failure_count >= max_retries:
            message = (
                f"{normalized_error}；自动重登录已连续失败 {max_retries} 次，"
                "已停止后台尝试，请手动登录"
            )
            next_retry_at = 0.0
        else:
            retry_interval = max(0, int(config.relogin_retry_interval_seconds))
            message = (
                f"{normalized_error}；第 {failure_count}/{max_retries} 次失败，"
                f"{retry_interval} 秒后重试"
            )
            next_retry_at = time.monotonic() + retry_interval
        _relogin_state["failure_count"] = failure_count
        _relogin_state["next_retry_at"] = next_retry_at
        _set_relogin_state("failed", message)
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
        if not preserve_relogin_state:
            config.campus_code = DEFAULT_CAMPUS_CODE
            config.campus_name = DEFAULT_CAMPUS_NAME
        _advance_session_generation()
        if not preserve_relogin_state:
            _reset_relogin_state_locked()
        global _restored_session_pending_validation
        _restored_session_pending_validation = False
        _persist_current_session()


def clear_login_state() -> None:
    """Clear credentials and all school-session state."""
    global _restored_session_pending_validation
    with _state_lock:
        config.combined_cookie = ""
        config.webvpn_cookie = ""
        config.authserver_cookie = ""
        config.backend_preference = config.BACKEND_AUTO
        config.active_backend = config.BACKEND_PRIMARY
        config.token = ""
        config.student_id = ""
        config.password = ""
        config.elective_batch_code = ""
        config.elective_batch_name = ""
        config.campus_code = DEFAULT_CAMPUS_CODE
        config.campus_name = DEFAULT_CAMPUS_NAME
        _advance_session_generation()
        _reset_relogin_state_locked()
        _restored_session_pending_validation = False
        try:
            clear_persisted_session()
        except OSError as exc:
            logger.warning("Unable to clear persisted school session: %s", exc)


def invalidate_school_session() -> None:
    """Drop expired school tokens while retaining credentials for recovery."""
    global _restored_session_pending_validation
    with _state_lock:
        config.combined_cookie = ""
        config.token = ""
        config.elective_batch_code = ""
        config.elective_batch_name = ""
        _advance_session_generation()
        _restored_session_pending_validation = False
        _persist_current_session()


def clear_elective_batch() -> None:
    """Clear a stale batch while preserving the valid school login session."""
    with _state_lock:
        config.elective_batch_code = ""
        config.elective_batch_name = ""


def update_backend_preference(preference: str) -> str:
    """Set and persist the user's preferred school backend."""
    with _state_lock:
        selected = backend_service.set_preference(preference)
        _persist_current_session()
        return selected


def get_session_snapshot() -> dict[str, str | bool | int]:
    """Return a consistent, password-free view of the current session."""
    with _state_lock:
        return {
            "logged_in": bool(config.token and config.combined_cookie),
            "student_id": str(config.student_id or ""),
            "batch_code": str(config.elective_batch_code or ""),
            "batch_name": str(config.elective_batch_name or ""),
            "campus_code": str(config.campus_code or DEFAULT_CAMPUS_CODE),
            "campus_name": str(config.campus_name or DEFAULT_CAMPUS_NAME),
            "relogin_in_progress": _relogin_state["status"] == "running",
            "relogin_status": str(_relogin_state["status"]),
            "relogin_message": str(_relogin_state["message"]),
            "relogin_started_at": str(_relogin_state["started_at"]),
            "relogin_finished_at": str(_relogin_state["finished_at"]),
            "relogin_max_attempts": int(_relogin_state["max_attempts"]),
            "relogin_failure_count": int(_relogin_state["failure_count"]),
            "relogin_max_retries": max(1, int(config.relogin_max_retries)),
            "relogin_retry_after": max(
                0,
                math.ceil(float(_relogin_state["next_retry_at"]) - time.monotonic()),
            ),
        }


def get_shared_session() -> tuple[bool, str, str]:
    """Return one immutable copy of the shared school session for the proxy.

    Reads ``config.combined_cookie`` and ``config.token`` atomically so the
    reverse proxy always injects the *current* server-side session on every
    request.  After an OCR automatic re-login the returned values change on
    the next call; the caller never holds a cached copy across requests.

    Returns ``(logged_in, combined_cookie, token)``.
    """
    with _state_lock:
        combined_cookie = str(config.combined_cookie or "")
        token = str(config.token or "")
    logged_in = bool(combined_cookie and token)
    return logged_in, combined_cookie, token


def get_shared_browser_session() -> tuple[bool, str, str, str]:
    """Return the shared session plus student id for the proxied school UI.

    The original school page checks ``sessionStorage`` before it makes its
    first API request.  The proxy uses this atomic snapshot to bootstrap that
    browser-side check from the same server-side session used by API mode.
    """
    with _state_lock:
        combined_cookie = str(config.combined_cookie or "")
        token = str(config.token or "")
        student_id = str(config.student_id or "")
    logged_in = bool(combined_cookie and token and student_id)
    return logged_in, combined_cookie, token, student_id


def merge_session_cookies(cookie_header: str) -> bool:
    """Fold school ``Set-Cookie`` values back into the shared session cookie.

    The reverse proxy must not leak ``Set-Cookie`` to the browser (the browser
    holds no meaningful school cookie and preserving a mismatched jar would
    promote session drift).  Instead the proxy hands every school ``Set-Cookie``
    here so cookies the school rotates in-flight (e.g. a fresh ``JSESSIONID``
    or ``_WEU``) stay merged into ``config.combined_cookie`` and the API mode
    keeps using the very same session.

    ``token``/``student_id``/batch state are intentionally left untouched, and
    when no valid in-memory session exists the merge is a safe no-op.
    """
    if not cookie_header or not str(cookie_header).strip():
        return False
    updates = []
    for name, value in _iter_set_cookie_pairs(str(cookie_header)):
        updates.append(f"{name}={value}")
    if not updates:
        return False
    merged = "; ".join(updates)
    with _state_lock:
        if not config.combined_cookie:
            return False
        config.combined_cookie = _combine_cookie_header(config.combined_cookie, merged)
        _persist_current_session()
    return True


def merge_backend_cookies(header_values: list[str], host: str) -> bool:
    """Merge cookies issued by WebVPN/CAS into the matching backend jar."""
    if not header_values:
        return False
    with _state_lock:
        changed = backend_service.merge_set_cookie(header_values, host)
        if changed:
            _persist_current_session()
        return changed


def _iter_set_cookie_pairs(
    cookie_header: str, names: tuple[str, ...] = ("route", "insert_cookie", "JSESSIONID", "_WEU")
):
    """Yield ``(name, value)`` pairs from a raw ``Set-Cookie`` header string.

    The school returns cookies mixed with path/expires directives (and commas
    inside ``Expires``), so they are matched piecemeal via the same regex
    approach that ``logic.parse_cookie`` already uses.
    """
    if not cookie_header:
        return
    name_pattern = "|".join(re.escape(name) for name in names)
    for match in re.finditer(rf"(?:^|[,;]\s*)({name_pattern})=([^;,]+)", cookie_header):
        yield match.group(1), match.group(2).strip()


def _combine_cookie_header(existing: str, additions: str) -> str:
    """Overlay ``additions`` onto ``existing`` without duplicating names.

    ``existing`` is the current ``config.combined_cookie`` (``name=value; ...``);
    ``additions`` is a ``; ``-joined string of fresh ``name=value`` pairs.
    Later values win (school rotation is authoritative).
    """
    values: dict[str, str] = {}
    for segment in str(existing).split(";"):
        segment = segment.strip()
        if "=" in segment:
            name, _, value = segment.partition("=")
            values[name.strip()] = value.strip()
    for segment in str(additions).split(";"):
        segment = segment.strip()
        if "=" in segment:
            name, _, value = segment.partition("=")
            values[name.strip()] = value.strip()
    return "; ".join(f"{name}={values[name]}" for name in values)


def refresh_elective_batch(
    student_id: str,
    token: str,
    adopt_school_campus: bool = False,
) -> str:
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

    batch_result = logic.fetch_elective_batch(
        normalized_student_id,
        normalized_token,
        combined_cookie,
    )
    batch_code, batch_name = batch_result
    school_campus_code = str(getattr(batch_result, "campus_code", "") or "").strip()
    with _state_lock:
        if (
            config.student_id != normalized_student_id
            or config.token != normalized_token
            or config.combined_cookie != combined_cookie
        ):
            raise RuntimeError("登录状态已变化，已丢弃过期批次结果")
        config.elective_batch_code = batch_code
        config.elective_batch_name = batch_name
        if adopt_school_campus and get_campus(school_campus_code) is not None:
            config.campus_code = school_campus_code
            config.campus_name = campus_name(school_campus_code)
    return batch_name


def set_current_campus(campus_code: str) -> dict[str, str | bool | int]:
    """Switch the catalog campus without changing the school login session."""
    selected = get_campus(campus_code)
    if selected is None:
        raise ValueError("不支持的校区代码")
    with _state_lock:
        if not config.token or not config.combined_cookie:
            raise RuntimeError("登录状态无效，请重新登录")
        config.campus_code = selected.code
        config.campus_name = selected.name
        return get_session_snapshot()


def restore_login_state() -> str:
    """Restore a persisted school session into the current process."""
    global _restored_session_pending_validation
    with _state_lock:
        if config.token and config.combined_cookie and config.student_id:
            return str(config.student_id)
    try:
        payload = load_persisted_session()
    except SessionStoreError as exc:
        logger.warning("Unable to restore local school session: %s", exc)
        clear_persisted_session()
        return ""
    if not isinstance(payload, dict):
        return ""
    required = ("student_id", "password", "token", "combined_cookie")
    if any(not str(payload.get(key, "")).strip() for key in required):
        clear_persisted_session()
        return ""
    with _state_lock:
        config.student_id = str(payload["student_id"])
        config.password = str(payload["password"])
        config.token = str(payload["token"])
        config.combined_cookie = str(payload["combined_cookie"])
        config.webvpn_cookie = str(payload.get("webvpn_cookie", ""))
        config.authserver_cookie = str(payload.get("authserver_cookie", ""))
        config.backend_preference = backend_service.normalize_preference(
            payload.get("backend_preference", config.BACKEND_AUTO)
        )
        config.active_backend = str(payload.get("active_backend", config.BACKEND_PRIMARY))
        config.elective_batch_code = str(payload.get("batch_code", ""))
        config.elective_batch_name = str(payload.get("batch_name", ""))
        _advance_session_generation()
        _restored_session_pending_validation = True
    return str(payload["student_id"])


def consume_restored_session_validation() -> bool:
    """Claim the one-time validation required for a session restored from disk."""
    global _restored_session_pending_validation
    with _state_lock:
        if not _restored_session_pending_validation:
            return False
        _restored_session_pending_validation = False
        return True


def restored_session_validation_pending() -> bool:
    with _state_lock:
        return _restored_session_pending_validation


def automatic_relogin_available() -> bool:
    """Return whether the retained credentials can start session recovery."""
    with _state_lock:
        return bool(
            config.student_id
            and config.password
            and not _restored_session_pending_validation
            and int(_relogin_state["failure_count"]) < max(1, int(config.relogin_max_retries))
        )


def attempt_ocr_relogin(
    max_attempts: int = config.ocr_relogin_max_attempts,
    *,
    student_id: str | None = None,
    password: str | None = None,
    backend: str | None = None,
) -> tuple[str, str, str, str]:
    """Solve a fresh captcha using one explicit login-page credential context."""
    with _state_lock:
        if student_id is not None:
            config.student_id = str(student_id).strip()
        if password is not None:
            config.password = str(password)
        if backend is not None:
            config.backend_preference = backend_service.set_preference(backend)
        if not config.student_id or not config.password:
            raise RuntimeError("没有可用于自动重登录的内存凭据")
    return logic.verify_vcode_login_flow(max_attempts=max_attempts)


def attempt_automatic_relogin(
    max_attempts: int = config.ocr_relogin_max_attempts,
    *,
    student_id: str | None = None,
    password: str | None = None,
    backend: str | None = None,
) -> tuple[bool, str]:
    """Run the login-page OCR flow for the supplied browser credential context."""
    with _state_lock:
        if student_id is not None:
            normalized_student_id = str(student_id).strip()
            if not re.fullmatch(r"\d{6,12}", normalized_student_id):
                return False, "浏览器会话中的自动登录凭据无效"
            config.student_id = normalized_student_id
        if password is not None:
            normalized_password = str(password)
            if not normalized_password.strip() or len(normalized_password) > 256:
                return False, "浏览器会话中的自动登录凭据无效"
            config.password = normalized_password
        if backend is not None:
            try:
                config.backend_preference = backend_service.set_preference(backend)
            except (KeyError, ValueError):
                return False, "浏览器会话中的访问后端无效"
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
            if not student_id or not password:
                return False, "没有可用于自动重登录的内存凭据"
            blocked_reason = _relogin_block_reason_locked()
            if blocked_reason:
                return False, blocked_reason

        _set_relogin_state(
            "running",
            f"正在使用 OCR 自动重新登录，最多识别 {max_attempts} 张验证码",
            max_attempts=max_attempts,
        )
        try:
            # The explicit browser context has already been copied into the
            # shared login context above.  The OCR helper then follows the
            # exact same fetch-image -> OCR -> password-encryption path as the
            # login page, while preserving compatibility with patched helpers.
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
                _reset_relogin_attempts_locked()
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


def start_automatic_relogin(
    *,
    student_id: str | None = None,
    password: str | None = None,
    backend: str | None = None,
) -> tuple[bool, str]:
    """Start one non-blocking OCR recovery worker for an explicit UI request."""
    global _automatic_relogin_worker
    with _automatic_relogin_worker_lock:
        with _state_lock:
            if config.token and config.combined_cookie:
                return True, "学校会话已经有效"
            if student_id is not None or password is not None:
                normalized_student_id = str(student_id or "").strip()
                normalized_password = str(password or "")
                if (
                    not re.fullmatch(r"\d{6,12}", normalized_student_id)
                    or not normalized_password.strip()
                    or len(normalized_password) > 256
                ):
                    return False, "浏览器会话中的自动登录凭据无效"
                config.student_id = normalized_student_id
                config.password = normalized_password
                if backend is not None:
                    config.backend_preference = backend_service.set_preference(backend)
                    if (
                        config.backend_preference == config.BACKEND_WEBVPN
                        and not backend_service.has_webvpn_cookies()
                    ):
                        return False, "请先完成 WebVPN 统一认证"
            if not config.student_id or not config.password:
                return False, "没有可用于自动重登录的内存凭据"
            blocked_reason = _relogin_block_reason_locked()
            if blocked_reason:
                return False, blocked_reason
            if _relogin_state["status"] == "running":
                return True, "正在自动重新登录，请稍候"
            _set_relogin_state(
                "running",
                f"正在使用 OCR 自动重新登录，最多识别 {config.ocr_relogin_max_attempts} 张验证码",
                max_attempts=config.ocr_relogin_max_attempts,
            )

        def worker() -> None:
            global _automatic_relogin_worker
            try:
                attempt_automatic_relogin(
                    config.ocr_relogin_max_attempts,
                    student_id=student_id,
                    password=password,
                    backend=backend,
                )
            finally:
                with _automatic_relogin_worker_lock:
                    _automatic_relogin_worker = None

        worker_thread = threading.Thread(
            target=worker,
            name="automatic-school-relogin",
            daemon=True,
        )
        _automatic_relogin_worker = worker_thread
        worker_thread.start()
        return True, "正在自动重新登录，请稍候"


__all__ = [
    "LOGIN_ERROR_MSG",
    "attempt_automatic_relogin",
    "attempt_ocr_relogin",
    "clear_elective_batch",
    "clear_login_state",
    "consume_restored_session_validation",
    "encrypt_password",
    "get_shared_session",
    "get_shared_browser_session",
    "get_session_snapshot",
    "invalidate_school_session",
    "merge_session_cookies",
    "merge_backend_cookies",
    "perform_school_login",
    "refresh_elective_batch",
    "restore_login_state",
    "restored_session_validation_pending",
    "automatic_relogin_available",
    "save_login_state",
    "start_automatic_relogin",
    "set_current_campus",
    "validate_login_params",
]
