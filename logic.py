"""School login, captcha, OCR, and cookie compatibility functions."""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

# OCR dependencies are imported lazily so manual first login can still start
# when optional recognition packages are unavailable.
import requests

import config
from project_paths import data_dir
from school_password import encrypt_school_password
from school_session import is_session_expired_response

REQUEST_TIMEOUT = (5, 15)
CAPTCHA_REQUEST_TIMEOUT = (3, 8)
MAX_CAPTCHA_BYTES = 2 * 1024 * 1024
CAPTCHA_WIDTH = 250
CAPTCHA_HEIGHT = 80
OCR_RETRY_DELAY_SECONDS = 0.25
CAPTCHA_UNAVAILABLE_KEYWORDS = (
    "非选课时间",
    "不在选课时间",
    "未开放",
    "尚未开放",
    "未开始",
    "尚未开始",
    "已结束",
    "已截止",
    "暂停",
    "关闭",
    "停选",
    "维护",
    "无选课批次",
    "没有选课批次",
)
logger = logging.getLogger(__name__)


class SchoolBatchSessionExpiredError(RuntimeError):
    """The school rejected a batch lookup because the session expired."""


class ElectiveBatchUnavailableError(RuntimeError):
    """The school did not expose an active elective batch."""


class CaptchaUnavailableError(RuntimeError):
    """The school explicitly reports that login captcha is currently unavailable."""


class CaptchaResponseError(RuntimeError):
    """The school captcha response is present but cannot be safely consumed."""


def _captcha_image_path() -> Path:
    return data_dir() / "img" / "image.jpg"


def _captcha_crop_dir() -> Path:
    return data_dir() / "img" / "crop"


def verify_vcode(
    max_attempts: int = config.ocr_relogin_max_attempts,
) -> tuple[str, str, str, str]:
    """Use OCR to solve a fresh click captcha with bounded retries."""
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts must be an integer")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    _captcha_crop_dir().mkdir(parents=True, exist_ok=True)
    if not config.student_id or not config.password:
        raise RuntimeError("缺少自动重登录所需的学号或密码")

    centers = []
    coordinates = ""
    solved_attempt = 0
    vtoken = ""
    cookie = ""
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            vtoken, cookie = get_new_image()
            centers = recognize_captcha_centers()
            coordinates = serialize_captcha_coordinates(centers)
            if coordinates:
                solved_attempt = attempt
                break
            last_error = RuntimeError("OCR did not return four valid coordinates")
        except CaptchaUnavailableError:
            logger.info("School captcha is unavailable; OCR relogin stopped without retrying")
            raise
        except (ImportError, ModuleNotFoundError):
            raise
        except Exception as exc:
            last_error = exc
        logger.warning(
            "OCR captcha attempt %s/%s failed: %s",
            attempt,
            max_attempts,
            last_error,
        )
        if attempt < max_attempts:
            time.sleep(min(OCR_RETRY_DELAY_SECONDS * attempt, 1.0))
    else:
        detail = type(last_error).__name__ if last_error else "unknown error"
        raise RuntimeError(f"OCR 连续 {max_attempts} 次识别失败 ({detail})")

    logger.info("OCR captcha solved on attempt %s/%s", solved_attempt, max_attempts)
    login_pwd = encrypt_school_password(config.password)

    parsed_cookie = parse_cookie(cookie)
    if not parsed_cookie:
        raise RuntimeError("验证码响应中缺少必要 Cookie")
    return vtoken, parsed_cookie, login_pwd, coordinates


def serialize_captcha_coordinates(centers: list) -> str:
    """Serialize exactly four validated click coordinates for the school form."""
    if not centers or len(centers) != 4:
        return ""

    coord_strings = []
    for coord in centers:
        if not isinstance(coord, (list, tuple)) or len(coord) != 2:
            return ""
        x, y = coord
        if isinstance(x, bool) or isinstance(y, bool):
            return ""
        if not isinstance(x, int) or not isinstance(y, int):
            return ""
        if not (0 <= x <= CAPTCHA_WIDTH and 0 <= y <= CAPTCHA_HEIGHT):
            return ""
        coord_strings.append(f"{x}-{y}")

    return ",".join(coord_strings)


def fetch_elective_batch(
    student_id: str,
    token: str,
    combined_cookie: str,
) -> tuple[str, str]:
    """Fetch the enrollment batch using one consistent session snapshot."""
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": ("zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5"),
        "Cookie": combined_cookie,
        "Host": "bkxk.szu.edu.cn",
        "Origin": "http://bkxk.szu.edu.cn",
        "Referer": ("http://bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 "
            "Safari/537.36 Edg/139.0.0.0"
        ),
        "token": token,
        "X-Requested-With": "XMLHttpRequest",
    }
    response = requests.post(
        f"{config.SCHOOL_BASE_URL}student/{student_id}.do",
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response_text = response.text
    try:
        payload = response.json()
    except ValueError as exc:
        if is_session_expired_response(
            status_code=response.status_code,
            text=response_text,
        ):
            raise SchoolBatchSessionExpiredError("学校登录状态已过期") from exc
        response.raise_for_status()
        raise RuntimeError("学校选课批次接口返回了非 JSON 响应") from exc

    response_code = payload.get("code") if isinstance(payload, dict) else None
    if is_session_expired_response(
        status_code=response.status_code,
        code=response_code,
        text=response_text,
    ):
        raise SchoolBatchSessionExpiredError("学校登录状态已过期")
    response.raise_for_status()
    if not isinstance(payload, dict):
        raise RuntimeError("学校选课批次响应格式异常")
    response_data = payload.get("data") or {}
    if not isinstance(response_data, dict):
        raise RuntimeError("学校选课批次响应数据格式异常")
    batch = response_data.get("electiveBatch") or {}
    if not isinstance(batch, dict):
        raise RuntimeError("学校选课批次字段格式异常")
    batch_code = batch.get("code")
    batch_name = batch.get("typeName") or ""
    if not batch_code:
        raise ElectiveBatchUnavailableError(payload.get("msg") or "学校当前未返回有效的选课批次")
    normalized_code = str(batch_code)
    normalized_name = str(batch_name)
    logger.info("Current enrollment batch: %s", normalized_name or "unknown")
    return normalized_code, normalized_name


def login(
    student_id: str,
    vtoken: str,
    login_pwd: str,
    coordinate_string: str,
    parsed_cookie: str,
) -> dict[str, Any]:
    """Establish a school session using the legacy login form contract."""
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": ("zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5"),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": parsed_cookie,
        "Host": "bkxk.szu.edu.cn",
        "Origin": "http://bkxk.szu.edu.cn",
        "Referer": ("http://bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 "
            "Safari/537.36 Edg/139.0.0.0"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }

    form_data = {
        "loginPwd": login_pwd,
        "loginName": student_id,
        "vtoken": vtoken,
        "verifyCode": coordinate_string,
    }

    response = requests.post(
        config.SCHOOL_BASE_URL + "student/check/login.do",
        data=form_data,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    try:
        payload = response.json()
    except ValueError:
        return {
            "success": False,
            "error_msg": "学校登录接口返回了非 JSON 响应",
            "cookie": None,
            "name": None,
        }
    if not isinstance(payload, dict):
        return {
            "success": False,
            "error_msg": "学校登录接口响应格式异常",
            "cookie": None,
            "name": None,
        }

    if payload.get("code") != "1":
        return {
            "success": False,
            "error_msg": payload.get("msg"),
            "cookie": None,
            "name": None,
        }

    login_cookie = response.headers.get("Set-Cookie")
    login_parsed_cookie = parse_login_cookie(login_cookie)
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    token = data.get("token") or ""
    name = data.get("name")
    if not token or not login_parsed_cookie:
        return {
            "success": False,
            "error_msg": "学校登录响应缺少会话信息",
            "cookie": None,
            "name": None,
        }
    logger.info("School session established for student ending in %s", student_id[-4:])
    return {
        "success": True,
        "error_msg": None,
        "cookie": login_parsed_cookie,
        "name": name,
        "token": token,
    }


def _extract_paddle_text(value: Any) -> str | None:
    """Best-effort extraction across PaddleOCR 2.x/3.x result shapes."""
    if isinstance(value, dict):
        texts = value.get("rec_texts")
        if isinstance(texts, list) and texts:
            return str(texts[0])
        for child in value.values():
            result = _extract_paddle_text(child)
            if result:
                return result
    elif isinstance(value, (list, tuple)):
        for child in value:
            result = _extract_paddle_text(child)
            if result:
                return result
    elif hasattr(value, "json"):
        raw = value.json
        result = _extract_paddle_text(raw() if callable(raw) else raw)
        if result:
            return result
    return None


def _recognize_target_with_paddle(image_path: str | Path) -> str | None:
    """Use PaddleOCR only when explicitly enabled to avoid model downloads."""
    if os.getenv("COURSE_SELECT_USE_PADDLE_OCR", "").strip() != "1":
        return None
    return _extract_paddle_text(_paddle_engine().predict(str(image_path)))


@lru_cache(maxsize=1)
def _paddle_engine():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="ch",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


@lru_cache(maxsize=1)
def _ddddocr_engines():
    from ddddocr import DetectionEngine, OCREngine

    return DetectionEngine(), OCREngine(beta=True)


def recognize_captcha_centers() -> list[list[int]]:
    """Recognize the four captcha targets and return click coordinates."""
    import cv2

    crop_dir = _captcha_crop_dir()
    image = cv2.imread(str(_captcha_image_path()))
    if image is None or image.shape[0] < CAPTCHA_HEIGHT or image.shape[1] < CAPTCHA_WIDTH:
        raise RuntimeError("验证码图片为空或尺寸异常")

    crop_dir.mkdir(parents=True, exist_ok=True)
    bottom_path = crop_dir / "bottom.jpg"
    top_path = crop_dir / "top.jpg"
    if not cv2.imwrite(str(bottom_path), image[25:80, 0:250]):
        raise RuntimeError("无法写入验证码候选区")
    if not cv2.imwrite(str(top_path), image[0:15, 80:135]):
        raise RuntimeError("无法写入验证码目标区")

    detector, ocr = _ddddocr_engines()
    with open(bottom_path, "rb") as handle:
        bottom = handle.read()
    with open(top_path, "rb") as handle:
        top = handle.read()

    recognition_text = "".join(ocr.predict(bottom).split())
    boxes = sorted(detector.predict(bottom), key=lambda item: item[0])
    if len(boxes) != len(recognition_text) or len(boxes) < 4:
        logger.warning(
            "OCR candidate count mismatch: text=%s boxes=%s",
            len(recognition_text),
            len(boxes),
        )
        return []

    centers = [[(x1 + x2) // 2, (y1 + y2) // 2 + 25] for x1, y1, x2, y2 in boxes]

    target_text = ""
    try:
        target_text = _recognize_target_with_paddle(top_path) or ""
    except Exception as exc:
        logger.warning("PaddleOCR unavailable; using ddddocr: %s", exc)
    if not target_text:
        target_text = ocr.predict(top)
    target_text = "".join(target_text.split())

    logger.debug("OCR target=%s candidates=%s", target_text, recognition_text)
    if len(target_text) != 4:
        return []

    result = []
    used_indexes = set()
    for target_char in target_text:
        matched_index = next(
            (
                index
                for index, candidate in enumerate(recognition_text)
                if index not in used_indexes and candidate == target_char
            ),
            None,
        )
        if matched_index is None:
            return []
        used_indexes.add(matched_index)
        result.append(centers[matched_index])
    return result


def _extract_captcha_message(payload: Any, response_text: str = "") -> str:
    """Extract a short school status message without depending on one response schema."""
    containers = [payload]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("msg", "message", "errorMessage", "error", "detail"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(response_text or "").strip()[:2000]


def _looks_like_captcha_unavailable(message: str) -> bool:
    normalized = str(message or "").strip()
    return any(keyword in normalized for keyword in CAPTCHA_UNAVAILABLE_KEYWORDS)


def _parse_captcha_token_response(response: requests.Response) -> str:
    """Return a validated token while preserving closed-window and transport failures."""
    response_text = str(getattr(response, "text", "") or "")
    try:
        payload = response.json()
    except ValueError:
        payload = None

    school_message = _extract_captcha_message(payload, response_text)
    if _looks_like_captcha_unavailable(school_message):
        raise CaptchaUnavailableError("school captcha endpoint is not open")

    response.raise_for_status()
    if not isinstance(payload, dict):
        raise CaptchaResponseError("school captcha token response is not JSON object")

    data = payload.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip() or len(token) > 512:
        raise CaptchaResponseError("school captcha token is missing or invalid")
    return token.strip()


def get_vtoken() -> str:
    time_stamp = int(time.time() * 1000)
    response = requests.post(
        config.SCHOOL_BASE_URL + f"student/4/vcode.do?timestamp={time_stamp}",
        timeout=CAPTCHA_REQUEST_TIMEOUT,
    )
    return _parse_captcha_token_response(response)


def _validate_captcha_image(image_data: bytes, content_type: str = "") -> None:
    normalized_type = content_type.lower()
    if (
        not image_data
        or len(image_data) > MAX_CAPTCHA_BYTES
        or not image_data.startswith(b"\xff\xd8\xff")
        or (normalized_type and not normalized_type.startswith("image/"))
    ):
        raise CaptchaResponseError(
            f"验证码图片响应异常(bytes={len(image_data)}, type={normalized_type or 'unknown'})"
        )


def get_new_image() -> tuple[str, str]:
    vtoken = get_vtoken()
    vcode_url = config.SCHOOL_BASE_URL + f"student/vcode/image.do?vtoken={vtoken}"
    response = requests.get(vcode_url, timeout=CAPTCHA_REQUEST_TIMEOUT)
    response.raise_for_status()
    cookie = response.headers.get("Set-Cookie", "")
    if not parse_cookie(cookie):
        raise CaptchaResponseError("验证码图片响应缺少必要 Cookie")
    _validate_captcha_image(
        response.content,
        response.headers.get("Content-Type", ""),
    )
    image_path = _captcha_image_path()
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(response.content)
    return vtoken, cookie


def _fetch_vtoken_and_image_once() -> dict[str, str]:
    captcha_base_url = config.SCHOOL_BASE_URL
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Host": "bkxk.szu.edu.cn",
        "Origin": "http://bkxk.szu.edu.cn",
        "Referer": f"{captcha_base_url}*default/index.do",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }

    timestamp = int(time.time() * 1000)
    token_response = requests.post(
        f"{captcha_base_url}student/4/vcode.do?timestamp={timestamp}",
        headers=headers,
        timeout=CAPTCHA_REQUEST_TIMEOUT,
    )
    vtoken = _parse_captcha_token_response(token_response)

    image_response = requests.get(
        f"{captcha_base_url}student/vcode/image.do?vtoken={vtoken}",
        headers=headers,
        timeout=CAPTCHA_REQUEST_TIMEOUT,
    )
    image_response.raise_for_status()
    image_data = image_response.content
    content_type = image_response.headers.get("Content-Type", "").lower()
    _validate_captcha_image(image_data, content_type)

    cookie = image_response.headers.get("Set-Cookie", "")
    if not parse_cookie(cookie):
        raise CaptchaResponseError("验证码图片响应缺少必要 Cookie")

    encoded = base64.b64encode(image_data).decode("ascii")
    return {
        "vtoken": vtoken,
        "cookie": cookie,
        "imageUrl": f"data:image/jpeg;base64,{encoded}",
    }


def fetch_vtoken_and_image(max_attempts: int = 3) -> dict[str, str]:
    """Fetch a click captcha while preserving terminal and transient failures."""
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError("max_attempts must be an integer")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _fetch_vtoken_and_image_once()
        except CaptchaUnavailableError:
            raise
        except (requests.RequestException, CaptchaResponseError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(0.25 * attempt)
    logger.warning("Captcha fetch failed after %s attempts: %s", max_attempts, last_error)
    if last_error is None:  # Defensive guard; max_attempts validation makes this unreachable.
        raise CaptchaResponseError("captcha fetch ended without a result")
    raise last_error


def _extract_named_cookies(cookie_string: str | None, names: tuple[str, ...]) -> str:
    """Extract named cookies without breaking on commas in Expires values."""
    if not cookie_string:
        return ""
    values = {}
    name_pattern = "|".join(re.escape(name) for name in names)
    for match in re.finditer(rf"(?:^|[,;]\s*)({name_pattern})=([^;,]+)", cookie_string):
        values[match.group(1)] = match.group(2).strip()
    return "; ".join(f"{name}={values[name]}" for name in names if name in values)


def parse_cookie(cookie_string: str | None) -> str:
    """解析 cookie 字符串，提取 route 和 insert_cookie。"""
    return _extract_named_cookies(cookie_string, ("route", "insert_cookie"))


def parse_login_cookie(login_cookie: str | None) -> str:
    """解析登录响应中的 cookie，提取 JSESSIONID 和 _WEU。"""
    return _extract_named_cookies(login_cookie, ("JSESSIONID", "_WEU"))
