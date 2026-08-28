"""School course-list requests.

The endpoint names and form fields in this module mirror the school client and
must remain wire-compatible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

import config
from campus import DEFAULT_CAMPUS_CODE, normalize_campus_code
from course_models import CoursesResponse
from services import backend_service

REQUEST_TIMEOUT = (5, 20)


@dataclass(frozen=True, slots=True)
class CourseQueryFailure:
    """A non-JSON or malformed school course response."""

    text: str
    status_code: int
    error: Exception


def get_headers() -> dict[str, str]:
    """Build headers from the latest in-memory school session."""
    profile = backend_service.active_profile()
    return backend_service.build_headers(
        profile,
        token=config.token,
        cookie=backend_service.cookie_header(profile),
        content_type="application/x-www-form-urlencoded; charset=UTF-8",
        accept="application/json, text/javascript, */*; q=0.01",
        referer=f"{profile.origin}/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={config.token}",
    )


def _query_courses(
    teaching_class_type: str,
    page: int,
    endpoint: str = "elective/programCourse.do",
) -> CoursesResponse | CourseQueryFailure:
    """Query one zero-based page without changing the school request contract."""
    campus_code = normalize_campus_code(
        getattr(config, "campus_code", DEFAULT_CAMPUS_CODE),
        fallback=DEFAULT_CAMPUS_CODE,
    )
    query_setting = {
        "data": {
            "studentCode": config.student_id,
            "campus": campus_code,
            "electiveBatchCode": config.elective_batch_code,
            "isMajor": "1",
            "teachingClassType": teaching_class_type,
            "checkConflict": "2",
            "checkCapacity": "2",
            "queryContent": "YCJX:2,MOOC:2,",
        },
        "pageSize": "10",
        "pageNumber": page,
        "order": "",
        "orderBy": "courseNumber",
    }
    def sender(**kwargs):
        kwargs.pop("method", None)
        return requests.post(**kwargs)

    profile = backend_service.active_profile()
    response = backend_service.request_with_failover(
        "POST",
        endpoint,
        sender=sender,
        data={
            "querySetting": json.dumps(
                query_setting,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
        timeout=REQUEST_TIMEOUT,
        token=config.token,
        cookie=backend_service.cookie_header(profile),
        content_type="application/x-www-form-urlencoded; charset=UTF-8",
        accept="application/json, text/javascript, */*; q=0.01",
        referer=f"{profile.origin}/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={config.token}",
    )
    try:
        return CoursesResponse.from_response(response)
    except (AttributeError, TypeError, ValueError) as exc:
        return CourseQueryFailure(response.text, response.status_code, exc)


def recommended_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query recommended/home-class courses (TJKC)."""
    return _query_courses("TJKC", page, "elective/recommendedCourse.do")


def programmed_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query courses inside the student's program (FANKC)."""
    return _query_courses("FANKC", page)


def non_programmed_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query courses outside the student's program (FAWKC)."""
    return _query_courses("FAWKC", page)


def public_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query university-wide public courses (XGXK)."""
    return _query_courses("XGXK", page)


def sport_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query physical-education courses (TYKC)."""
    return _query_courses("TYKC", page)


def minor_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query minor-program courses (FXKC)."""
    return _query_courses("FXKC", page)


def mooc_course(page: int) -> CoursesResponse | CourseQueryFailure:
    """Query online courses (MOOC)."""
    return _query_courses("MOOC", page)


__all__ = [
    "CourseQueryFailure",
    "REQUEST_TIMEOUT",
    "minor_course",
    "mooc_course",
    "non_programmed_course",
    "programmed_course",
    "public_course",
    "recommended_course",
    "sport_course",
]
