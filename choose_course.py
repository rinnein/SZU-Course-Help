"""Wire-compatible school enrollment and enrolled-course requests."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from campus import DEFAULT_CAMPUS_CODE, normalize_campus_code
from school_session import is_session_expired_response
from services import backend_service

REQUEST_TIMEOUT = (5, 20)
logger = logging.getLogger(__name__)


def _build_session() -> requests.Session:
    """Create a session with connection pooling and transport-layer retries.

    Only connection-level and transient HTTP failures are retried here; the
    school's business payload (success / capacity full / terminal) is still
    classified by the caller in ``services.enroll_service``.
    """
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.3,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(("GET", "POST")),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_session = _build_session()

# Keep the module-level request seam used by older integrations and tests while
# retaining a replaceable session seam for withdrawal callers.
_session.post = lambda **kwargs: requests.post(**kwargs)


class SchoolSessionExpiredError(RuntimeError):
    """Raised when the school responds with an expired-session signal."""


def _request_headers(combined_cookie: str, token: str) -> dict[str, str]:
    return backend_service.build_headers(
        backend_service.active_profile(), token=token, cookie=combined_cookie
    )


def _school_request(
    path: str,
    *,
    data=None,
    params=None,
    token: str = "",
    cookie: str | None = None,
    request_sender=None,
):
    def sender(**kwargs):
        kwargs.pop("method", None)
        kwargs.pop("json", None)
        return (request_sender or _session.post)(**kwargs)

    return backend_service.request_with_failover(
        "POST",
        path,
        sender=sender,
        data=data,
        params=params,
        token=token,
        cookie=cookie,
        timeout=REQUEST_TIMEOUT,
    )


def query_enrolled_courses(
    combined_cookie: str,
    token: str,
) -> list[dict[str, Any]]:
    """Return the current student's selected courses from the school system."""
    timestamp = int(time.time() * 1000)
    response = _school_request(
        f"elective/courseResult.do?timestamp={timestamp}&studentCode={config.student_id}",
        token=token,
        cookie=combined_cookie,
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


def submit_course_selection(
    class_id: str,
    teaching_class_type: str,
    campus_code: str = DEFAULT_CAMPUS_CODE,
):
    """Submit one course-selection request using the school's legacy payload."""
    normalized_campus = normalize_campus_code(campus_code)
    form_data = {
        "addParam": (
            r"""{"data":{"operationType":"1","studentCode":%s,"electiveBatchCode":%s,"teachingClassId":%s,"isMajor":"1","campus":"%s","teachingClassType":%s,"chooseVolunteer":"1"}}"""  # noqa: UP031 - exact legacy wire template
            % (
                str(config.student_id),
                config.elective_batch_code,
                class_id,
                normalized_campus,
                teaching_class_type,
            )
        )
    }
    logger.info(
        "Submitting enrollment request: class=%s type=%s campus=%s",
        class_id,
        teaching_class_type,
        normalized_campus,
    )
    return _school_request(
        "elective/volunteer.do",
        data=form_data,
        token=config.token,
        cookie=backend_service.cookie_header(backend_service.active_profile()),
    )


def delete_course_selection(class_id: str):
    """Withdraw one selected volunteer using the confirmed school contract."""
    form_data = {
        "deleteParam": json.dumps(
            {
                "data": {
                    "operationType": "2",
                    "studentCode": str(config.student_id),
                    "electiveBatchCode": str(config.elective_batch_code),
                    "teachingClassId": str(class_id),
                    "isMajor": "1",
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    }
    return _school_request(
        "elective/deleteVolunteer.do",
        data=form_data,
        token=config.token,
        cookie=backend_service.cookie_header(backend_service.active_profile()),
        request_sender=_session.post,
    )


__all__ = [
    "REQUEST_TIMEOUT",
    "SchoolSessionExpiredError",
    "query_enrolled_courses",
    "submit_course_selection",
    "delete_course_selection",
]
