"""Typed models for school catalog responses and frontend projections."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


def _as_string(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def time_signature(value: Any) -> str:
    """Normalize the stable day/period portion of a school schedule string."""
    text = _as_string(value).strip()
    if not text:
        return ""
    match = re.search(
        r"(星期[一二三四五六日天]|周[一二三四五六日天]).{0,12}?([0-9]+\s*[-至]\s*[0-9]+节?)", text
    )
    if match:
        day = match.group(1).replace("周", "星期")
        periods = re.sub(r"\s+", "", match.group(2)).replace("至", "-")
        return f"{day}-{periods}"
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True, slots=True)
class TeachingClass:
    """One teaching-class option nested under a school course."""

    ext_info: str
    teaching_class_id: str
    course_number: str
    is_mooc: str
    is_test: str
    class_capacity: str
    teaching_place: str
    number_of_first_volunteer: str
    is_main_select_object: str
    course_index: str
    teacher_name: str
    department_name: str
    sport_name: str
    grade_name: str
    course_type_name: str
    course_nature_name: str
    is_choose: str
    course_total_number: str
    is_full: str
    is_conflict: str
    number_of_selected: str
    has_test: str
    remote_teach: str
    time_signature: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeachingClass:
        return cls(
            ext_info=_as_string(data.get("extInfo")),
            teaching_class_id=_as_string(data.get("teachingClassID")),
            course_number=_as_string(data.get("courseNumber")),
            is_mooc=_as_string(data.get("isMooc")),
            is_test=_as_string(data.get("isTest")),
            class_capacity=_as_string(data.get("classCapacity")),
            teaching_place=_as_string(data.get("teachingPlace")),
            number_of_first_volunteer=_as_string(data.get("numberOfFirstVolunteer")),
            is_main_select_object=_as_string(data.get("isMainSelectObject")),
            course_index=_as_string(data.get("courseIndex")),
            teacher_name=_as_string(data.get("teacherName")),
            department_name=_as_string(data.get("departmentName")),
            sport_name=_as_string(data.get("sportName")),
            grade_name=_as_string(data.get("gradeName")),
            course_type_name=_as_string(data.get("courseTypeName")),
            course_nature_name=_as_string(data.get("courseNatureName")),
            is_choose=_as_string(data.get("isChoose")),
            course_total_number=_as_string(data.get("courseTotalNumber")),
            is_full=_as_string(data.get("isFull")),
            is_conflict=_as_string(data.get("isConflict")),
            number_of_selected=_as_string(data.get("numberOfSelected")),
            has_test=_as_string(data.get("hasTest")),
            remote_teach=_as_string(data.get("remoteTeach")),
            time_signature=time_signature(data.get("teachingPlace")),
        )


@dataclass(frozen=True, slots=True)
class SchoolCourse:
    """One course and all teaching-class options returned by the school."""

    teaching_classes: list[TeachingClass]
    course_number: str
    credit: str
    course_name: str
    department_name: str
    sport_name: str
    campus_name: str
    number: int
    selected: bool
    credit_type: str
    credit_type_name: str
    course_total_number: str
    type_name: str
    course_type: str
    course_nature_name: str
    hours: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchoolCourse:
        return cls(
            teaching_classes=[
                TeachingClass.from_dict(item) for item in _as_dict_list(data.get("tcList"))
            ],
            course_number=_as_string(data.get("courseNumber")),
            credit=_as_string(data.get("credit")),
            course_name=_as_string(data.get("courseName")),
            department_name=_as_string(data.get("departmentName")),
            sport_name=_as_string(data.get("sportName")),
            campus_name=_as_string(data.get("campusName")),
            number=_as_int(data.get("number")),
            selected=_as_bool(data.get("selected")),
            credit_type=_as_string(data.get("creditType")),
            credit_type_name=_as_string(data.get("creditTypeName")),
            course_total_number=_as_string(data.get("courseTotalNumber")),
            type_name=_as_string(data.get("typeName")),
            course_type=_as_string(data.get("type")),
            course_nature_name=_as_string(data.get("courseNatureName")),
            hours=_as_string(data.get("hours")),
        )


@dataclass(frozen=True, slots=True)
class CoursesResponse:
    """Complete course response returned by the school API."""

    total_count: int
    data_list: list[SchoolCourse]
    msg: str
    code: str
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoursesResponse:
        if not isinstance(data, dict):
            raise TypeError("course response must be an object")
        return cls(
            total_count=_as_int(data.get("totalCount")),
            data_list=[
                SchoolCourse.from_dict(item) for item in _as_dict_list(data.get("dataList"))
            ],
            msg=_as_string(data.get("msg")),
            code=_as_string(data.get("code")),
            timestamp=_as_string(data.get("timestamp")),
        )

    @classmethod
    def from_json(cls, json_data: str) -> CoursesResponse:
        return cls.from_dict(json.loads(json_data))

    @classmethod
    def from_response(cls, response: Any) -> CoursesResponse:
        return cls.from_dict(response.json())

    def to_course_list_response(self) -> CourseListResponse:
        return CourseListResponse.from_school_response(self)


@dataclass(frozen=True, slots=True)
class TeachingClassView:
    """Fields required by the frontend for one teaching class."""

    teaching_class_id: str
    is_mooc: str
    class_capacity: str
    teaching_place: str
    course_index: str
    teacher_name: str
    sport_name: str
    is_choose: str
    course_total_number: str
    is_full: str
    is_conflict: str
    number_of_selected: str
    course_number: str
    time_signature: str

    @classmethod
    def from_school_class(cls, item: TeachingClass) -> TeachingClassView:
        return cls(
            teaching_class_id=item.teaching_class_id,
            is_mooc=item.is_mooc,
            class_capacity=item.class_capacity,
            teaching_place=item.teaching_place,
            course_index=item.course_index,
            teacher_name=item.teacher_name,
            sport_name=item.sport_name,
            is_choose=item.is_choose,
            course_total_number=item.course_total_number,
            is_full=item.is_full,
            is_conflict=item.is_conflict,
            number_of_selected=item.number_of_selected,
            course_number=item.course_number,
            time_signature=item.time_signature,
        )


@dataclass(frozen=True, slots=True)
class CourseView:
    """Frontend projection of one course."""

    teaching_classes: list[TeachingClassView]
    course_number: str
    course_name: str
    department_name: str
    sport_name: str
    number: int
    selected: bool
    credit: str
    course_type_name: str
    course_nature_name: str
    campus_name: str = ""

    @classmethod
    def from_school_course(cls, course: SchoolCourse) -> CourseView:
        return cls(
            teaching_classes=[
                TeachingClassView.from_school_class(item) for item in course.teaching_classes
            ],
            course_number=course.course_number,
            course_name=course.course_name,
            department_name=course.department_name,
            sport_name=course.sport_name,
            campus_name=course.campus_name,
            number=course.number,
            selected=course.selected,
            credit=course.credit,
            course_type_name=course.type_name,
            course_nature_name=course.course_nature_name,
        )

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "tcList": [asdict(item) for item in self.teaching_classes],
            "course_number": self.course_number,
            "course_name": self.course_name,
            "department_name": self.department_name,
            "sport_name": self.sport_name,
            "campus_name": self.campus_name,
            "number": self.number,
            "selected": self.selected,
            "credit": self.credit,
            "course_type_name": self.course_type_name,
            "course_nature_name": self.course_nature_name,
        }


@dataclass(frozen=True, slots=True)
class CourseListResponse:
    """Frontend projection of a school course page."""

    total_count: int
    courses: list[CourseView]
    msg: str
    is_error: bool

    @classmethod
    def from_school_response(cls, response: CoursesResponse) -> CourseListResponse:
        return cls(
            total_count=response.total_count,
            courses=[CourseView.from_school_course(item) for item in response.data_list],
            msg=response.msg,
            is_error=str(response.code) != "1",
        )

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "total_count": self.total_count,
            "courses": [course.to_api_dict() for course in self.courses],
            "msg": self.msg,
            "is_error": self.is_error,
        }


__all__ = [
    "CourseListResponse",
    "CourseView",
    "CoursesResponse",
    "SchoolCourse",
    "TeachingClass",
    "TeachingClassView",
]
