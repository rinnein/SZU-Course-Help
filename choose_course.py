"""Wire-compatible school enrollment and enrolled-course requests."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

import config
from school_session import is_session_expired_response

REQUEST_TIMEOUT = (5, 20)
logger = logging.getLogger(__name__)


class SchoolSessionExpiredError(RuntimeError):
    """Raised when the school responds with an expired-session signal."""


def _request_headers(combined_cookie: str, token: str) -> dict[str, str]:
    return {
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


def query_enrolled_courses(
    combined_cookie: str,
    token: str,
) -> list[dict[str, Any]]:
    """Return the current student's selected courses from the school system."""
    timestamp = int(time.time() * 1000)
    response = requests.post(
        url=(
            f"{config.SCHOOL_BASE_URL}elective/courseResult.do"
            f"?timestamp={timestamp}&studentCode={config.student_id}"
        ),
        headers=_request_headers(combined_cookie, token),
        timeout=REQUEST_TIMEOUT,
    )

    if is_session_expired_response(
        status_code=response.status_code,
        text=response.text,
    ):
        raise SchoolSessionExpiredError("school session expired")
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        if is_session_expired_response(text=response.text):
            raise SchoolSessionExpiredError("school returned the login page") from exc
        raise ValueError("school enrolled-course response was not JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("school enrolled-course response must be an object")
    if is_session_expired_response(
        status_code=response.status_code,
        code=payload.get("code"),
        text=response.text,
    ):
        raise SchoolSessionExpiredError("school session expired")

    data_list = payload.get("dataList") or []
    if not isinstance(data_list, list):
        raise ValueError("school enrolled-course dataList must be a list")

    return data_list


def submit_course_selection(class_id: str, teaching_class_type: str):
    """Submit one course-selection request using the school's legacy payload."""
    headers = _request_headers(config.combined_cookie, config.token)
    form_data = {
        "addParam": (
            r"""{"data":{"operationType":"1","studentCode":%s,"electiveBatchCode":%s,"teachingClassId":%s,"isMajor":"1","campus":"01","teachingClassType":%s,"chooseVolunteer":"1"}}"""  # noqa: UP031 - exact legacy wire template
            % (
                str(config.student_id),
                config.elective_batch_code,
                class_id,
                teaching_class_type,
            )
        )
    }
    logger.info(
        "Submitting enrollment request: class=%s type=%s",
        class_id,
        teaching_class_type,
    )
    return requests.post(
        url=config.SCHOOL_BASE_URL + "elective/volunteer.do",
        data=form_data,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )


__all__ = [
    "REQUEST_TIMEOUT",
    "SchoolSessionExpiredError",
    "query_enrolled_courses",
    "submit_course_selection",
]
