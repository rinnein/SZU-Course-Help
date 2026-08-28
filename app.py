"""FastAPI controller and local static-file server."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import FileResponse, JSONResponse

import config
import logic
from campus import (
    DEFAULT_CAMPUS_CODE,
    campus_name,
    campus_options_payload,
    get_campus,
)
from card_key import verify_card_key
from logging_config import configure_logging
from project_paths import resource_path
from security.key_manager import (
    KeyManagementError,
    generate_card_key,
    get_or_create_key_pair,
)
from services import backend_service, cart_service, proxy_service, webvpn_auth_service
from services.auth_service import (
    LOGIN_ERROR_MSG,
    attempt_automatic_relogin,
    clear_elective_batch,
    clear_login_state,
    consume_restored_session_validation,
    encrypt_password,
    get_session_snapshot,
    invalidate_school_session,
    perform_school_login,
    refresh_elective_batch,
    restore_login_state,
    restored_session_validation_pending,
    save_login_state,
    set_current_campus,
    update_backend_preference,
    validate_login_params,
)
from services.cache_service import get_no_cache_headers
from services.course_service import (
    COURSE_QUERY_REJECTED,
    COURSE_RESPONSE_INVALID,
    COURSE_WINDOW_CLOSED,
    SESSION_EXPIRED,
    get_enrolled_courses,
    get_unsupported_message,
    is_supported_type,
    query_courses,
)
from services.enroll_service import (
    get_enroll_progress,
    get_enroll_task_state,
    is_enroll_task_running,
    pause_enroll_task,
    remove_cart_course,
    resume_enroll_task,
    start_enroll_worker,
    stop_enroll_task,
)
from services.proxy_service import SCHOOL_HOST, clear_proxy_cookie_mirror
from services.timetable_service import build_timetable

proxy_request = proxy_service.proxy_request

SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8000
UI_ASSET_BUILD = "20260829.2"
UI_CACHE_TOKEN = secrets.token_urlsafe(8)
logger = logging.getLogger(__name__)


def _preferred_port() -> int:
    try:
        port = int(os.getenv("COURSE_SELECT_PORT", str(DEFAULT_SERVER_PORT)))
    except ValueError:
        return DEFAULT_SERVER_PORT
    return port if 1 <= port <= 65535 else DEFAULT_SERVER_PORT


def _find_available_port(preferred: int, attempts: int = 20) -> int:
    """Choose a local port early so terminal output and browser URL stay aligned."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_candidate = min(preferred + attempts - 1, 65535)
    for candidate in range(preferred, last_candidate + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((SERVER_HOST, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"端口 {preferred} 至 {last_candidate} 均被占用")


SERVER_PORT = _find_available_port(_preferred_port())

_runtime_prefill = {"student_id": "", "card_key": ""}


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    """Start process-local services when the ASGI application starts."""
    _start_runtime_services()
    yield


app = FastAPI(title="深大抢课助手 API", version="3.4.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{SERVER_PORT}",
        f"http://localhost:{SERVER_PORT}",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    student_id: str = Field(min_length=6, max_length=12, pattern=r"^\d+$")
    password: str = Field(min_length=1, max_length=256)
    card_key: str = Field(min_length=1, max_length=2048)
    vtoken: str = Field(min_length=1, max_length=512)
    verify_code: list[list[int]] = Field(
        alias="verifyCode",
        min_length=4,
        max_length=4,
    )
    cookie: str = Field(min_length=1, max_length=8192)
    backend: str = Field(default=config.BACKEND_AUTO, pattern=r"^(auto|primary|webvpn)$")


class BackendSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(pattern=r"^(auto|primary|webvpn)$")


class WebVPNAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_path: str = Field(
        default="/xsxkapp/sys/xsxkapp/*default/index.do",
        min_length=1,
        max_length=512,
    )


class ProxyBrowserOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_path: str = Field(
        default="/xsxkapp/sys/xsxkapp/*default/index.do",
        min_length=1,
        max_length=512,
    )


class CardKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str = Field(min_length=6, max_length=12, pattern=r"^\d+$")


class ApiMessage(BaseModel):
    message: str
    is_error: bool


class CartCourse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=128)
    course_type: str = Field(
        alias="type",
        min_length=1,
        max_length=16,
        pattern=r"^[A-Z]+$",
    )
    name: str = Field(min_length=1, max_length=512)
    campus_code: str = Field(default=DEFAULT_CAMPUS_CODE, pattern=r"^\d{2}$")
    campus_name: str = Field(default="", max_length=64)
    teaching_place: str = Field(default="", max_length=512)
    course_name: str = Field(default="", max_length=256)
    teacher_name: str = Field(default="", max_length=128)
    is_choose: str = Field(default="", max_length=8)
    is_conflict: str = Field(default="", max_length=8)
    is_full: str = Field(default="", max_length=8)
    status: str = Field(default="", max_length=16)

    @property
    def type(self) -> str:
        """Expose the school wire name to legacy persistence/service code."""
        return self.course_type


class EnrollmentStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_phase: bool = False


class CampusSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campus_code: str = Field(pattern=r"^\d{2}$")


static_dir = resource_path("static_dist")
_runtime_start_lock = threading.Lock()
_runtime_started = False


def _start_runtime_services() -> None:
    """Restore persisted state and start process-local background services once."""
    global _runtime_started
    with _runtime_start_lock:
        if _runtime_started:
            return
        restored = restore_login_state()
        if restored:
            logger.info("Restored persisted school session for student ending in %s", restored[-4:])
        keep_alive = threading.Thread(
            target=_keep_alive_loop,
            name="session-keep-alive",
            daemon=True,
        )
        keep_alive.start()
        logger.info("Session keep-alive started (every %ds)", KEEP_ALIVE_INTERVAL_SECONDS)
        _runtime_started = True


def configure_runtime_prefill(student_id: str, card_key: str) -> None:
    """Set one-process login defaults generated by the terminal flow."""
    _runtime_prefill["student_id"] = str(student_id).strip()
    _runtime_prefill["card_key"] = str(card_key).strip()


def get_server_url() -> str:
    return f"http://{SERVER_HOST}:{SERVER_PORT}"


def get_frontend_url(path: str = "/") -> str:
    """Return a process-unique frontend URL that cannot reuse stale HTML."""
    normalized_path = f"/{str(path).lstrip('/')}"
    return f"{get_server_url()}{normalized_path}?ui={UI_CACHE_TOKEN}"


def get_login_url() -> str:
    return get_frontend_url("/login")


def _cart_from_row(row: dict) -> CartCourse:
    return CartCourse(
        id=row.get("id", ""),
        course_type=row.get("type", ""),
        name=row.get("name", ""),
        campus_code=row.get("campus_code", DEFAULT_CAMPUS_CODE),
        campus_name=(
            row.get("campus_name", "") or campus_name(row.get("campus_code", DEFAULT_CAMPUS_CODE))
        ),
        teaching_place=row.get("teaching_place", ""),
        course_name=row.get("course_name", ""),
        teacher_name=row.get("teacher_name", ""),
        status=row.get("status", ""),
    )


def _api_error(
    status_code: int,
    message: str,
    error_code: str,
    *,
    retryable: bool,
    requires_manual_login: bool = False,
    **extra,
):
    """Return one stable error envelope for frontend recovery decisions."""
    return JSONResponse(
        status_code=status_code,
        content={
            "message": message,
            "is_error": True,
            "error_code": error_code,
            "retryable": retryable,
            "requires_manual_login": requires_manual_login,
            **extra,
        },
        headers=get_no_cache_headers(),
    )


def _not_logged_in_response(message: str | None = None, error_code: str | None = None):
    snapshot = get_session_snapshot()
    task_state = get_enroll_task_state()
    if snapshot["relogin_in_progress"] or (
        task_state["running"]
        and not task_state["paused"]
        and snapshot["relogin_status"] == "failed"
    ):
        return _api_error(
            409,
            "学校会话正在自动恢复，请稍候；课程和抢课任务不会丢失",
            "SESSION_RECOVERY_IN_PROGRESS",
            retryable=True,
        )
    return _api_error(
        401,
        message or "登录状态无效，请重新登录",
        error_code or "NOT_LOGGED_IN",
        retryable=False,
        requires_manual_login=True,
    )


def _session_payload() -> dict:
    snapshot = get_session_snapshot()
    task_state = get_enroll_task_state()
    batch_name = str(snapshot["batch_name"])
    phase = config.classify_elective_phase(batch_name)
    return {
        **snapshot,
        "phase": phase,
        "automatic_enroll_allowed": phase == config.PHASE_AUTOMATIC,
        "task_running": task_state["running"],
        "task_paused": task_state["paused"],
        "task_pause_acknowledged": task_state["pause_acknowledged"],
        "task_pause_reason": task_state["pause_reason"],
        "task_pause_source": task_state["pause_source"],
        "task_paused_at": task_state["paused_at"],
        "task_stopping": task_state["stopping"],
        "task_stopping_reason": task_state["stopping_reason"],
        "ui_cache_token": UI_CACHE_TOKEN,
        "campus_options": campus_options_payload(),
        **backend_service.backend_payload(),
    }


@app.get("/api/bootstrap")
async def api_bootstrap():
    """Return local runtime defaults and safety guidance for the login page."""
    return JSONResponse(
        content={
            "student_id": _runtime_prefill["student_id"],
            "card_key": _runtime_prefill["card_key"],
            "ui_cache_token": UI_CACHE_TOKEN,
            "ui_asset_build": UI_ASSET_BUILD,
            "phase_notice": "预选阶段只用于浏览和整理课程；自动抢课仅用于复选、正选或补选阶段。",
            **backend_service.backend_payload(),
        },
        headers=get_no_cache_headers(),
    )


@app.post("/api/card_key", status_code=status.HTTP_200_OK)
async def api_generate_card_key(req: CardKeyRequest):
    """Issue a student-bound Card Key V3 straight from the login page.

    Generating the key locally from the student number removes the terminal-only
    step so the Web UI and the sign-in screen share the same entry point.
    """
    student_id = req.student_id.strip()
    if not re.fullmatch(r"^\d{6,12}$", student_id):
        return JSONResponse(
            status_code=400,
            content={"message": "学号必须是 6 至 12 位数字", "is_error": True},
        )
    try:
        private_key = get_or_create_key_pair()
        card_key = generate_card_key(student_id, private_key)
        return {"card_key": card_key, "student_id": student_id}
    except (KeyManagementError, OSError, ValueError) as exc:
        logger.warning("Card-key generation failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"message": f"卡密生成失败: {exc}", "is_error": True},
        )


@app.post("/api/backend/select")
async def api_select_backend(req: BackendSelectRequest):
    """Select the school backend used by subsequent login and API requests."""
    preference = update_backend_preference(req.backend)
    payload = backend_service.backend_payload()
    if preference == config.BACKEND_WEBVPN and not payload["webvpn_authenticated"]:
        return _api_error(
            409,
            "请先完成 WebVPN 统一认证",
            "WEBVPN_AUTH_REQUIRED",
            retryable=True,
            **payload,
        )
    return JSONResponse(content=payload, headers=get_no_cache_headers())


@app.post("/api/login", status_code=status.HTTP_200_OK)
async def api_login(user: LoginRequest):
    """Verify the local card key, then establish a school session."""
    try:
        update_backend_preference(user.backend)
        if user.backend == config.BACKEND_WEBVPN and not backend_service.has_webvpn_cookies():
            return _api_error(
                409,
                "请先完成 WebVPN 统一认证",
                "WEBVPN_AUTH_REQUIRED",
                retryable=True,
            )
        student_id = user.student_id.strip()
        err = validate_login_params(
            student_id,
            user.password,
            user.card_key,
            user.verify_code,
            user.vtoken,
            user.cookie,
        )
        if err:
            return JSONResponse(
                status_code=400,
                content={"message": err, "is_error": True},
            )

        if not verify_card_key(student_id, user.card_key):
            return JSONResponse(
                status_code=400,
                content={"message": LOGIN_ERROR_MSG, "is_error": True},
            )

        login_pwd = encrypt_password(user.password)
        centres_string = logic.serialize_captcha_coordinates(user.verify_code)
        semi_cookie = logic.parse_cookie(user.cookie)
        if not centres_string or not semi_cookie:
            return JSONResponse(
                status_code=400,
                content={"message": LOGIN_ERROR_MSG, "is_error": True},
            )

        logger.info("Starting school login for student ending in %s", student_id[-4:])
        login_result = await asyncio.to_thread(
            perform_school_login,
            student_id,
            user.vtoken,
            login_pwd,
            centres_string,
            semi_cookie,
        )
        if not login_result["success"]:
            logger.warning("School rejected login: %s", login_result.get("error_msg"))
            return JSONResponse(
                status_code=400,
                content={"message": LOGIN_ERROR_MSG, "is_error": True},
            )

        save_login_state(
            login_result["cookie"],
            semi_cookie,
            student_id,
            user.password,
            login_result["token"],
        )

        message = "登录成功"
        try:
            await asyncio.to_thread(refresh_elective_batch, student_id, config.token, True)
        except Exception as exc:
            logger.warning("Login succeeded but batch refresh failed: %s", exc)
            message = "登录成功，选课批次暂未读取到，请稍后刷新"
        return ApiMessage(message=message, is_error=False)
    except Exception as exc:
        logger.exception("Unexpected login failure: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"message": LOGIN_ERROR_MSG, "is_error": True},
        )


@app.get("/api/captcha")
async def api_captcha(backend: str | None = Query(default=None)):
    """Fetch one manual-login captcha and expose a finite, classified failure state."""
    try:
        if backend:
            update_backend_preference(backend)
        # Manual refresh is the retry boundary. One server attempt prevents stale
        # requests from accumulating after a browser timeout.
        result = await asyncio.to_thread(logic.fetch_vtoken_and_image, 1)
        return JSONResponse(content=result, headers=get_no_cache_headers())
    except logic.CaptchaUnavailableError:
        logger.info("School captcha is unavailable in the current window")
        return _api_error(
            409,
            "学校当前未提供登录验证码，可能尚未开放选课、正在切换阶段或处于维护时段。请稍后手动重试。",
            "CAPTCHA_UNAVAILABLE",
            retryable=True,
        )
    except backend_service.WebVPNAuthenticationRequiredError:
        return _api_error(
            409,
            "主站暂时无法访问，请先完成 WebVPN 统一认证后重试",
            "WEBVPN_AUTH_REQUIRED",
            retryable=True,
        )
    except requests.Timeout:
        logger.warning("School captcha request timed out")
        return _api_error(
            504,
            "连接学校验证码服务超时，本次加载已停止。请检查网络后手动重试。",
            "CAPTCHA_TIMEOUT",
            retryable=True,
        )
    except requests.RequestException as exc:
        logger.warning("School captcha network failure: %s", type(exc).__name__)
        return _api_error(
            503,
            "暂时无法连接学校验证码服务，本次加载已停止。请检查网络或稍后手动重试。",
            "CAPTCHA_NETWORK_ERROR",
            retryable=True,
        )
    except (logic.CaptchaResponseError, ValueError, RuntimeError) as exc:
        logger.warning("School captcha response rejected: %s", type(exc).__name__)
        return _api_error(
            502,
            "学校验证码响应异常，本次加载已停止。请稍后手动重试；持续失败时请确认学校系统是否开放。",
            "CAPTCHA_INVALID_RESPONSE",
            retryable=True,
        )
    except Exception as exc:
        logger.exception("Unexpected captcha failure: %s", exc)
        return _api_error(
            500,
            "验证码服务发生意外错误，本次加载已停止。请重新启动程序后再试。",
            "CAPTCHA_INTERNAL_ERROR",
            retryable=False,
        )


CAPTCHA_SOLVE_MAX_RETRIES = 20


@app.post("/api/captcha/solve", status_code=status.HTTP_200_OK)
async def api_captcha_solve(payload: dict):
    """Run local OCR on the captcha image and return four click coordinates.

    If OCR fails on the provided image, the server automatically fetches fresh
    captchas and retries.  When a retry succeeds the new captcha data
    (``vtoken``, ``cookie``, ``imageUrl``) is returned so the frontend can
    update its state.
    """
    import base64

    image_url = str(payload.get("imageUrl", "")).strip()
    if not image_url.startswith("data:image/"):
        return JSONResponse(
            status_code=400,
            content={"message": "缺少验证码图片数据", "is_error": True},
        )

    current_vtoken = str(payload.get("vtoken", "")).strip()
    current_cookie = str(payload.get("cookie", "")).strip()
    current_image_url = image_url

    for attempt in range(1, CAPTCHA_SOLVE_MAX_RETRIES + 1):
        try:
            header, encoded = current_image_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
            image_path = logic._captcha_image_path()
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(image_bytes)

            centers = await asyncio.to_thread(logic.recognize_captcha_centers)
            if centers and len(centers) == 4:
                response = {"points": centers, "message": ""}
                if attempt > 1:
                    response["captcha"] = {
                        "vtoken": current_vtoken,
                        "cookie": current_cookie,
                        "imageUrl": current_image_url,
                    }
                return JSONResponse(content=response)

            logger.info(
                "Captcha solve attempt %s/%s: OCR returned %s points",
                attempt,
                CAPTCHA_SOLVE_MAX_RETRIES,
                len(centers) if centers else 0,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            logger.warning("OCR dependency unavailable: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"points": [], "message": f"OCR 依赖不可用: {exc}"},
            )
        except Exception as exc:
            logger.warning(
                "Captcha solve attempt %s/%s failed: %s", attempt, CAPTCHA_SOLVE_MAX_RETRIES, exc
            )

        # Fetch a fresh captcha for the next retry
        if attempt < CAPTCHA_SOLVE_MAX_RETRIES:
            try:
                fresh = await asyncio.to_thread(logic.fetch_vtoken_and_image, 1)
                current_vtoken = fresh["vtoken"]
                current_cookie = fresh["cookie"]
                current_image_url = fresh["imageUrl"]
            except Exception as exc:
                logger.warning("Failed to fetch fresh captcha for retry: %s", exc)
                break

    return JSONResponse(
        status_code=200,
        content={
            "points": [],
            "message": "OCR 多次尝试未能识别，请手动点击或刷新验证码重试",
            "captcha": {
                "vtoken": current_vtoken,
                "cookie": current_cookie,
                "imageUrl": current_image_url,
            },
        },
    )


@app.get("/api/session")
async def api_session():
    payload = _session_payload()
    if payload["logged_in"] and consume_restored_session_validation():
        try:
            await asyncio.to_thread(
                refresh_elective_batch,
                str(payload["student_id"]),
                config.token,
            )
            payload = _session_payload()
        except logic.SchoolBatchSessionExpiredError:
            invalidate_school_session()
            return _not_logged_in_response(
                "保存的登录会话已失效，请重新登录",
                "SESSION_RESTORE_EXPIRED",
            )
        except logic.ElectiveBatchUnavailableError as exc:
            clear_elective_batch()
            logger.info("Restored session is valid but no batch is available: %s", exc)
        except (requests.Timeout, requests.RequestException) as exc:
            logger.info("Restored session validation deferred after network issue: %s", exc)
        except Exception as exc:
            logger.warning("Restored session validation failed without expiring session: %s", exc)
    _record_frontend_session_success()
    return JSONResponse(
        content=payload,
        headers=get_no_cache_headers(),
    )


@app.post("/api/session/refresh")
async def api_session_refresh():
    """Refresh the school-provided batch without touching enrollment state."""
    snapshot = get_session_snapshot()
    if not snapshot["logged_in"] or not snapshot["student_id"]:
        return _not_logged_in_response()

    try:
        await asyncio.to_thread(
            refresh_elective_batch,
            str(snapshot["student_id"]),
            config.token,
        )
    except logic.SchoolBatchSessionExpiredError:
        logger.info("Batch refresh detected expiry; starting OCR recovery")
        recovered, error = await asyncio.to_thread(
            attempt_automatic_relogin,
            config.ocr_relogin_max_attempts,
        )
        if not recovered:
            logger.warning("OCR session recovery failed during batch refresh: %s", error)
            return _api_error(
                401,
                "登录已过期且 OCR 自动重登录失败，请手动登录",
                "SESSION_RECOVERY_FAILED",
                retryable=False,
                requires_manual_login=True,
            )
    except backend_service.WebVPNAuthenticationRequiredError:
        return _api_error(
            409,
            "主站暂时无法访问，请先完成 WebVPN 统一认证后重试",
            "WEBVPN_AUTH_REQUIRED",
            retryable=True,
        )
    except logic.ElectiveBatchUnavailableError as exc:
        clear_elective_batch()
        logger.info("School currently exposes no elective batch: %s", exc)
        return _api_error(
            409,
            "登录仍然有效，但学校当前未开放可用的选课批次。你的本地清单已保留，可稍后重新检查。",
            "BATCH_UNAVAILABLE",
            retryable=True,
            session=_session_payload(),
        )
    except requests.Timeout:
        logger.warning("Batch refresh timed out")
        return _api_error(
            504,
            "检查开放状态超时，登录状态和本地清单不受影响，请稍后重试",
            "SCHOOL_TIMEOUT",
            retryable=True,
        )
    except requests.RequestException as exc:
        logger.warning("Batch refresh network failure: %s", exc)
        return _api_error(
            503,
            "暂时无法连接学校选课系统，登录状态和本地清单不受影响",
            "SCHOOL_NETWORK_ERROR",
            retryable=True,
        )
    except Exception as exc:
        logger.exception("Batch refresh failed: %s", exc)
        return _api_error(
            502,
            "学校返回的选课批次信息暂时无法识别，请稍后重新检查",
            "SCHOOL_BATCH_INVALID",
            retryable=True,
        )

    return JSONResponse(
        content={**_session_payload(), "message": "开放状态已更新"},
        headers=get_no_cache_headers(),
    )


@app.post("/api/logout")
async def api_logout():
    if is_enroll_task_running():
        return JSONResponse(
            status_code=409,
            content={"message": "抢课任务运行中，暂不能清除登录态", "is_error": True},
        )
    webvpn_auth_service.clear_proxy_cookies()
    clear_login_state()
    response = JSONResponse(
        content=ApiMessage(message="已清除本地登录态", is_error=False).model_dump(),
        headers=get_no_cache_headers(),
    )
    return clear_proxy_cookie_mirror(response)


@app.post("/api/session/campus")
async def api_switch_campus(request: CampusSwitchRequest):
    """Select the campus used by subsequent catalog requests."""
    if not config.token or not config.combined_cookie:
        return _not_logged_in_response()
    if get_campus(request.campus_code) is None:
        return _api_error(
            400,
            "不支持的校区，请刷新页面后重试",
            "INVALID_CAMPUS",
            retryable=False,
        )
    try:
        await asyncio.to_thread(set_current_campus, request.campus_code)
    except RuntimeError:
        return _not_logged_in_response()
    logger.info("Course catalog campus changed to %s", campus_name(request.campus_code))
    return JSONResponse(
        content={**_session_payload(), "message": f"已切换到{campus_name(request.campus_code)}"},
        headers=get_no_cache_headers(),
    )


@app.get("/api/school/courses")
async def api_school_courses(
    course_type: str = Query(
        default="TJKC",
        alias="type",
        min_length=1,
        max_length=16,
    ),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=10, ge=1, le=100),
):
    if not config.token or not config.combined_cookie:
        return _not_logged_in_response()
    if page_size != 10:
        return _api_error(
            400,
            "学校接口固定每页 10 门课程",
            "INVALID_PAGE_SIZE",
            retryable=False,
        )
    normalized_type = course_type.strip().upper()
    if not is_supported_type(normalized_type):
        return _api_error(
            400,
            get_unsupported_message(normalized_type),
            "UNSUPPORTED_COURSE_TYPE",
            retryable=False,
        )

    snapshot = get_session_snapshot()
    phase = config.classify_elective_phase(str(snapshot["batch_name"]))
    if phase == config.PHASE_CLOSED:
        return _api_error(
            409,
            "你已登录，但当前不在开放选课时间，学校课程目录暂不可用。可稍后重新检查开放状态。",
            "COURSE_WINDOW_CLOSED",
            retryable=True,
            phase=phase,
        )
    if not snapshot["batch_code"]:
        return _api_error(
            409,
            "你已登录，但暂未读取到有效选课批次。请先重新检查开放状态。",
            "BATCH_UNAVAILABLE",
            retryable=True,
            phase=phase,
        )
    # WebUI 对用户使用 1-based 页码；学校接口的 pageNumber 从 0 开始。
    school_page = page - 1
    try:
        success, data, _ = await asyncio.to_thread(
            query_courses,
            normalized_type,
            school_page,
        )
        if not success and data == SESSION_EXPIRED:
            logger.info("Course query detected expiry; starting OCR recovery")
            recovered, error = await asyncio.to_thread(
                attempt_automatic_relogin, config.ocr_relogin_max_attempts
            )
            if not recovered:
                logger.warning("OCR session recovery failed: %s", error)
                return _api_error(
                    401,
                    "登录已过期且 OCR 自动重登录失败，请手动登录",
                    "SESSION_RECOVERY_FAILED",
                    retryable=False,
                    requires_manual_login=True,
                )
            success, data, _ = await asyncio.to_thread(
                query_courses,
                normalized_type,
                school_page,
            )
        if success:
            content = data.to_api_dict() if hasattr(data, "to_api_dict") else data
            return JSONResponse(content=content, headers=get_no_cache_headers())
        failure_map = {
            COURSE_WINDOW_CLOSED: (
                409,
                "学校提示当前不在开放选课时间，课程目录暂不可用。请重新检查开放状态。",
                "COURSE_WINDOW_CLOSED",
            ),
            COURSE_QUERY_REJECTED: (
                502,
                "学校暂时拒绝了课程目录请求，请稍后刷新；本地清单不受影响。",
                "SCHOOL_COURSE_REJECTED",
            ),
            COURSE_RESPONSE_INVALID: (
                502,
                "学校课程接口返回了无法识别的数据，请稍后刷新。",
                "SCHOOL_RESPONSE_INVALID",
            ),
        }
        response_status, message, error_code = failure_map.get(
            data,
            (502, "获取课程列表失败，请稍后刷新", "SCHOOL_COURSE_ERROR"),
        )
        return _api_error(
            response_status,
            message,
            error_code,
            retryable=True,
        )
    except backend_service.WebVPNAuthenticationRequiredError:
        return _api_error(
            409,
            "主站暂时无法访问，请先完成 WebVPN 统一认证后重试",
            "WEBVPN_AUTH_REQUIRED",
            retryable=True,
        )
    except requests.Timeout:
        logger.warning("Course-list endpoint timed out")
        return _api_error(
            504,
            "学校课程接口响应超时，请稍后刷新；本地清单不受影响。",
            "SCHOOL_TIMEOUT",
            retryable=True,
        )
    except requests.RequestException as exc:
        logger.warning("Course-list endpoint network failure: %s", exc)
        return _api_error(
            503,
            "暂时无法连接学校选课系统，请检查网络后刷新；本地清单不受影响。",
            "SCHOOL_NETWORK_ERROR",
            retryable=True,
        )
    except Exception as exc:
        logger.exception("Course-list endpoint failed: %s", exc)
        return _api_error(
            502,
            "获取课程列表时出现未预期错误，请稍后刷新；本地清单不受影响。",
            "SCHOOL_COURSE_ERROR",
            retryable=True,
        )


@app.get("/api/school/enrolled")
async def api_school_enrolled():
    """Return courses already selected by the current student."""
    if not config.token or not config.combined_cookie:
        return _not_logged_in_response()
    try:
        success, data = await asyncio.to_thread(get_enrolled_courses)
        if not success and data == SESSION_EXPIRED:
            logger.info("Selected-course query detected expiry; starting OCR recovery")
            recovered, error = await asyncio.to_thread(
                attempt_automatic_relogin,
                config.ocr_relogin_max_attempts,
            )
            if not recovered:
                logger.warning("OCR session recovery failed: %s", error)
                return _api_error(
                    401,
                    "登录已过期且 OCR 自动重登录失败，请手动登录",
                    "SESSION_RECOVERY_FAILED",
                    retryable=False,
                    requires_manual_login=True,
                )
            success, data = await asyncio.to_thread(get_enrolled_courses)
        if success:
            return JSONResponse(
                content={
                    "courses": data,
                    "total_count": len(data),
                    "timetable": build_timetable(data),
                },
                headers=get_no_cache_headers(),
            )
        return _api_error(
            502,
            str(data) or "获取已选课程失败，请稍后刷新",
            "SCHOOL_ENROLLED_ERROR",
            retryable=True,
        )
    except backend_service.WebVPNAuthenticationRequiredError:
        return _api_error(
            409,
            "主站暂时无法访问，请先完成 WebVPN 统一认证后重试",
            "WEBVPN_AUTH_REQUIRED",
            retryable=True,
        )
    except requests.Timeout:
        logger.warning("Selected-course endpoint timed out")
        return _api_error(
            504,
            "学校已选课程接口响应超时，请稍后刷新",
            "SCHOOL_TIMEOUT",
            retryable=True,
        )
    except requests.RequestException as exc:
        logger.warning("Selected-course endpoint network failure: %s", exc)
        return _api_error(
            503,
            "暂时无法连接学校选课系统，请检查网络后刷新",
            "SCHOOL_NETWORK_ERROR",
            retryable=True,
        )
    except Exception as exc:
        logger.exception("Selected-course endpoint failed: %s", exc)
        return _api_error(
            502,
            "获取已选课程失败，请稍后刷新",
            "SCHOOL_ENROLLED_ERROR",
            retryable=True,
        )


@app.post("/api/courses/add")
async def api_cart_add(cart: CartCourse):
    if is_enroll_task_running():
        return JSONResponse(
            status_code=409,
            content={"message": "抢课任务运行中，暂不能修改清单", "is_error": True},
        )
    result = cart_service.add_course(cart)
    return ApiMessage(message=result["message"], is_error=not result["success"])


@app.post("/api/courses/delete")
async def api_cart_delete(
    course_id: str = Query(alias="id", min_length=1, max_length=128),
):
    result = remove_cart_course(course_id)
    if not result["success"] and str(result.get("error_code", "")).startswith("ENROLL_TASK_"):
        return _api_error(
            409,
            str(result["message"]),
            str(result["error_code"]),
            retryable=result.get("error_code") == "ENROLL_TASK_PAUSE_PENDING",
        )
    if not result["success"]:
        return ApiMessage(message=str(result["message"]), is_error=True)
    return JSONResponse(
        content={
            "message": result["message"],
            "is_error": False,
            "task_stopping": bool(result.get("task_stopping")),
            "progress": get_enroll_progress(),
        },
        headers=get_no_cache_headers(),
    )


@app.post("/api/courses/retry")
async def api_cart_retry(
    course_id: str = Query(alias="id", min_length=1, max_length=128),
):
    """Return an explicitly failed item to PENDING without duplicating it."""
    if is_enroll_task_running():
        return JSONResponse(
            status_code=409,
            content={"message": "抢课任务运行中，暂不能重新排队", "is_error": True},
        )
    result = cart_service.retry_failed_course(course_id)
    return ApiMessage(message=result["message"], is_error=not result["success"])


@app.get("/api/courses/dblist")
@app.post("/api/courses/dblist", include_in_schema=False)
async def api_cart_list(
    status: str = Query(default="", max_length=16),
) -> list[CartCourse]:
    return [_cart_from_row(row) for row in cart_service.get_courses_by_status(status)]


@app.get("/api/courses/sorted")
async def api_cart_sorted() -> list[CartCourse]:
    return [_cart_from_row(row) for row in cart_service.get_all_sorted()]


@app.post("/api/enroll/courses")
async def api_start_enroll(
    request: EnrollmentStartRequest,
):
    """Start one guarded background worker without changing school request fields."""
    snapshot = get_session_snapshot()
    if not snapshot["logged_in"] or not snapshot["student_id"]:
        return _not_logged_in_response()

    try:
        await asyncio.to_thread(
            refresh_elective_batch,
            str(snapshot["student_id"]),
            config.token,
        )
    except Exception as exc:
        logger.warning("Could not verify enrollment phase before task start: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "message": "无法向学校确认当前选课批次，未启动抢课，请刷新后重试",
                "is_error": True,
            },
        )

    snapshot = get_session_snapshot()
    batch_name = str(snapshot["batch_name"]).strip()
    if not snapshot["batch_code"]:
        return JSONResponse(
            status_code=503,
            content={
                "message": "学校未返回有效选课批次，未启动抢课",
                "is_error": True,
            },
        )
    block_reason = config.automatic_enroll_block_reason(batch_name)
    if block_reason:
        return JSONResponse(
            status_code=409,
            content={
                "message": block_reason,
                "is_error": True,
            },
        )
    if not request.confirmed_phase:
        return JSONResponse(
            status_code=400,
            content={"message": "请确认当前为复选、正选或补选阶段", "is_error": True},
        )
    if not cart_service.get_courses_by_status("PENDING"):
        return JSONResponse(
            status_code=400,
            content={"message": "购物车中没有待抢课程", "is_error": True},
        )
    try:
        worker_started = start_enroll_worker()
    except Exception as exc:
        logger.exception("Could not start enrollment worker: %s", exc)
        return _api_error(
            500,
            "后台抢课任务启动失败，清单已保留，请稍后重试",
            "ENROLL_WORKER_START_FAILED",
            retryable=True,
        )
    if not worker_started:
        return JSONResponse(
            status_code=409,
            content={"message": "已有抢课任务正在运行", "is_error": True},
        )

    return ApiMessage(message="抢课任务已在后台启动", is_error=False)


@app.get("/api/enroll/status")
async def api_enroll_status():
    return JSONResponse(
        content=get_enroll_progress(),
        headers=get_no_cache_headers(),
    )


@app.post("/api/enroll/pause")
async def api_pause_enroll():
    success, message = pause_enroll_task()
    if not success:
        task_state = get_enroll_task_state()
        error_code = "ENROLL_TASK_STOPPING" if task_state["stopping"] else "ENROLL_TASK_NOT_RUNNING"
        return _api_error(
            409,
            message,
            error_code,
            retryable=False,
        )
    progress = get_enroll_progress()
    if progress["paused"] and not progress["pause_acknowledged"]:
        message = "暂停请求已提交，正在等待当前学校请求结束"
    return JSONResponse(
        content={"message": message, "is_error": False, "progress": progress},
        headers=get_no_cache_headers(),
    )


@app.post("/api/enroll/resume")
async def api_resume_enroll():
    task_state = get_enroll_task_state()
    if not task_state["running"]:
        return _api_error(
            409,
            "当前没有可继续的抢课任务",
            "ENROLL_TASK_NOT_RUNNING",
            retryable=False,
        )
    if task_state["stopping"]:
        return _api_error(
            409,
            task_state["stopping_reason"] or "抢课任务正在结束，请稍候",
            "ENROLL_TASK_STOPPING",
            retryable=True,
        )

    snapshot = get_session_snapshot()
    if snapshot["relogin_in_progress"]:
        return _api_error(
            409,
            "正在自动重新登录，请等待恢复完成后再继续任务",
            "SESSION_RECOVERY_IN_PROGRESS",
            retryable=True,
        )
    if not snapshot["logged_in"]:
        return _api_error(
            409,
            "学校登录尚未恢复，请先完成手动登录，再返回清单继续任务",
            "LOGIN_REQUIRED_FOR_RESUME",
            retryable=False,
            requires_manual_login=True,
        )

    try:
        await asyncio.to_thread(
            refresh_elective_batch,
            str(snapshot["student_id"]),
            config.token,
        )
    except logic.SchoolBatchSessionExpiredError:
        recovered, error = await asyncio.to_thread(
            attempt_automatic_relogin,
            config.ocr_relogin_max_attempts,
        )
        if not recovered:
            logger.warning("OCR session recovery failed before task resume: %s", error)
            return _api_error(
                409,
                "学校登录已过期且自动恢复失败，任务仍保持暂停；请先手动登录",
                "LOGIN_REQUIRED_FOR_RESUME",
                retryable=False,
                requires_manual_login=True,
            )
    except logic.ElectiveBatchUnavailableError:
        clear_elective_batch()
        return _api_error(
            409,
            "学校当前未返回可用选课批次，任务仍保持暂停",
            "BATCH_UNAVAILABLE",
            retryable=True,
        )
    except requests.Timeout:
        return _api_error(
            504,
            "确认当前选课批次超时，任务仍保持暂停，请稍后再试",
            "SCHOOL_TIMEOUT",
            retryable=True,
        )
    except requests.RequestException:
        return _api_error(
            503,
            "暂时无法连接学校系统确认选课批次，任务仍保持暂停",
            "SCHOOL_NETWORK_ERROR",
            retryable=True,
        )
    except Exception as exc:
        logger.exception("Could not verify enrollment phase before task resume: %s", exc)
        return _api_error(
            503,
            "无法确认当前选课批次，任务仍保持暂停，请稍后再试",
            "PHASE_CHECK_FAILED",
            retryable=True,
        )

    refreshed = get_session_snapshot()
    if not refreshed["batch_code"]:
        return _api_error(
            409,
            "学校未返回有效选课批次，任务仍保持暂停",
            "BATCH_UNAVAILABLE",
            retryable=True,
        )
    block_reason = config.automatic_enroll_block_reason(str(refreshed["batch_name"]))
    if block_reason:
        return _api_error(
            409,
            f"{block_reason}；原任务仍保持暂停",
            "PHASE_NOT_ALLOWED",
            retryable=True,
        )

    success, message = resume_enroll_task()
    if not success:
        return _api_error(
            409,
            message,
            "ENROLL_TASK_NOT_RUNNING",
            retryable=False,
        )
    return JSONResponse(
        content={"message": message, "is_error": False, "progress": get_enroll_progress()},
        headers=get_no_cache_headers(),
    )


@app.post("/api/enroll/stop")
async def api_stop_enroll():
    """Request the running enrollment worker to stop gracefully.

    Returns 404 when no task is running (nothing to stop), 202 when the stop
    flag was set.  The worker observes the flag at the next checkpoint and
    exits through its normal ``finally`` block.
    """
    if not is_enroll_task_running():
        return JSONResponse(
            status_code=404,
            content={"message": "当前没有正在运行的抢课任务", "is_error": True},
        )
    if not stop_enroll_task():
        return JSONResponse(
            status_code=409,
            content={"message": "抢课任务状态已变化，请重试", "is_error": True},
        )
    return JSONResponse(
        status_code=202,
        content={"message": "已请求停止抢课任务，等待当前轮次结束", "is_error": False},
    )


@app.get("/api/health")
async def api_health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().astimezone().isoformat(),
        "version": app.version,
    }


def _safe_static_file(relative_path: str) -> Path | None:
    try:
        candidate = (static_dir / relative_path).resolve()
        candidate.relative_to(static_dir)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


@app.api_route(
    "/proxy/{school_host}/{school_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def api_school_proxy(school_host: str, school_path: str, request: Request):
    """Reverse-proxy arbitrary ``bkxk.szu.edu.cn`` pages through the shared session.

    The browser never performs its own school login here; every request reuses
    the server-side ``config.combined_cookie``/``config.token`` established by
    the API-mode login, so switching between the API workbench and the proxied
    school page never logs the session out (the school kicks all previous
    sessions on a fresh login).
    """
    if school_host.lower() != SCHOOL_HOST:
        raise HTTPException(status_code=404, detail="不支持的代理目标")
    return await proxy_request(request, school_path)


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    no_cache = get_no_cache_headers()
    candidates = []
    if full_path:
        candidates.extend((full_path, f"{full_path}.html", f"{full_path}/index.html"))
    else:
        candidates.append("index.html")

    for relative_path in candidates:
        target = _safe_static_file(relative_path)
        if target:
            return FileResponse(target, headers=no_cache)

    root = _safe_static_file("index.html")
    if root:
        return FileResponse(root, headers=no_cache)
    raise HTTPException(status_code=404, detail="前端文件未找到")


def open_browser() -> None:
    webbrowser.open(get_login_url())


KEEP_ALIVE_INTERVAL_SECONDS = 60
FRONTEND_SESSION_HEARTBEAT_GRACE_SECONDS = KEEP_ALIVE_INTERVAL_SECONDS + 10
_frontend_session_heartbeat_lock = threading.Lock()
_last_frontend_session_success = 0.0


def _record_frontend_session_success() -> None:
    """Record a successful browser session read for keep-alive coordination."""
    global _last_frontend_session_success
    with _frontend_session_heartbeat_lock:
        _last_frontend_session_success = time.monotonic()


def _frontend_session_heartbeat_is_active() -> bool:
    """Return whether the browser has recently completed a session request."""
    with _frontend_session_heartbeat_lock:
        last_success = _last_frontend_session_success
    return (
        last_success > 0
        and time.monotonic() - last_success < FRONTEND_SESSION_HEARTBEAT_GRACE_SECONDS
    )


def _keep_alive_once() -> None:
    """Refresh the school session so it does not expire while the user is idle."""
    snapshot = get_session_snapshot()
    if not snapshot["logged_in"] or not snapshot["student_id"]:
        return
    if _frontend_session_heartbeat_is_active():
        logger.debug("Keep-alive skipped: frontend session heartbeat is active")
        return
    student_id = str(snapshot["student_id"])
    token = str(config.token)
    try:
        refresh_elective_batch(student_id, token)
        logger.info("Keep-alive: school session refreshed")
    except logic.SchoolBatchSessionExpiredError:
        logger.info("Keep-alive: session expired; starting OCR recovery")
        if restored_session_validation_pending():
            invalidate_school_session()
            logger.info("Keep-alive: restored session requires manual validation")
            return
        recovered, error = attempt_automatic_relogin(config.ocr_relogin_max_attempts)
        if not recovered:
            logger.warning("Keep-alive OCR recovery failed: %s", error)
    except logic.ElectiveBatchUnavailableError as exc:
        logger.info("Keep-alive: school session alive but no batch: %s", exc)
    except (requests.Timeout, requests.RequestException) as exc:
        logger.info("Keep-alive network issue (session unaffected): %s", exc)
    except Exception as exc:
        logger.warning("Keep-alive unexpected failure: %s", exc)


def _keep_alive_loop() -> None:
    """Run periodic session refresh on a daemon thread until the process exits."""
    while True:
        threading.Event().wait(KEEP_ALIVE_INTERVAL_SECONDS)
        try:
            _keep_alive_once()
        except Exception as exc:
            logger.warning("Keep-alive loop error: %s", exc)


def start_server() -> None:
    import uvicorn

    configure_logging()
    if os.getenv("COURSE_SELECT_NO_BROWSER", "").strip() != "1":
        timer = threading.Timer(1.2, open_browser)
        timer.daemon = True
        timer.start()
    reload_enabled = os.getenv("COURSE_SELECT_DEV", "").strip() == "1"
    if reload_enabled:
        logger.info("Backend development reload enabled")
    uvicorn.run(
        "app:app" if reload_enabled else app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=reload_enabled,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload_enabled else None,
    )


if __name__ == "__main__":
    start_server()
