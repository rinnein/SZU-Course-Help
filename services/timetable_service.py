"""Convert selected-course rows into a conservative weekly timetable."""

from __future__ import annotations

import re
from typing import Any

DAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
DAY_INDEX = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
}
MAX_PERIOD = 14

_DAY_CHARACTERS = "一二三四五六日天"
_DAY_PATTERN = re.compile(rf"星期\s*([{_DAY_CHARACTERS}])|(?<!\d)周\s*([{_DAY_CHARACTERS}])")
_PERIOD_PATTERN = re.compile(r"第?\s*(\d{1,2})\s*(?:[-–—~～至]\s*(\d{1,2}))?\s*节")
_WEEK_COMPONENT = r"\d{1,2}\s*(?:[-–—~～至]\s*\d{1,2})?\s*周(?:\s*[（(][单双][）)])?"
_WEEK_LIST = rf"{_WEEK_COMPONENT}(?:\s*[,，、]\s*{_WEEK_COMPONENT})*"
_DAY_TOKEN = rf"(?:星期\s*|(?<!\d)周\s*)[{_DAY_CHARACTERS}]"
_DAY_GROUP = (
    rf"{_DAY_TOKEN}"
    rf"(?:\s*[,，、/&和及]\s*(?:(?:星期\s*|(?<!\d)周\s*)?[{_DAY_CHARACTERS}]))*"
)
_SCHEDULE_PATTERN = re.compile(
    rf"(?:(?P<weeks>{_WEEK_LIST})\s*)?"
    rf"(?P<days>{_DAY_GROUP})\s*"
    rf"第?\s*(?P<start>\d{{1,2}})\s*"
    rf"(?:[-–—~～至]\s*(?P<end>\d{{1,2}}))?\s*节"
)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_location(value: str) -> str:
    return str(value or "").strip(" \t,，;；·|-/")


def _course_display(course: dict[str, Any]) -> dict[str, str]:
    """Keep only stable display fields from an untrusted school row."""
    return {
        "teaching_class_id": str(course.get("teaching_class_id") or ""),
        "course_name": str(course.get("course_name") or "未命名课程"),
        "teacher_name": str(course.get("teacher_name") or ""),
        "teaching_place": str(course.get("teaching_place") or ""),
        "course_number": str(course.get("course_number") or ""),
        "course_type_name": str(course.get("course_type_name") or ""),
        "campus_name": str(course.get("campus_name") or ""),
    }


def _parse_schedule(schedule: str) -> tuple[list[dict[str, Any]], str]:
    """Extract every weekday/period pair from one school schedule string."""
    normalized = re.sub(r"<br\s*/?>", "；", str(schedule or ""), flags=re.IGNORECASE)
    matches = list(_SCHEDULE_PATTERN.finditer(normalized))
    parsed_entries: list[dict[str, Any]] = []
    invalid_period_reason = ""

    for index, match in enumerate(matches):
        start_period = int(match.group("start"))
        end_period = int(match.group("end") or match.group("start"))
        if not (1 <= start_period <= end_period <= MAX_PERIOD):
            invalid_period_reason = invalid_period_reason or f"节次超出 1-{MAX_PERIOD} 范围"
            continue

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        location = _clean_text(_clean_location(normalized[match.end() : next_start]))
        raw_segment = _clean_text(normalized[match.start() : next_start]).strip(" ,，;；|")
        weeks = _clean_text(match.group("weeks") or "")
        day_characters = list(
            dict.fromkeys(re.findall(rf"[{_DAY_CHARACTERS}]", match.group("days")))
        )

        for day_character in day_characters:
            day = DAY_INDEX[day_character]
            parsed_entries.append(
                {
                    "day": day,
                    "day_name": DAY_NAMES[day - 1],
                    "start_period": start_period,
                    "end_period": end_period,
                    "weeks": weeks,
                    "location": location,
                    "raw_schedule": raw_segment,
                }
            )

    if parsed_entries:
        return parsed_entries, ""
    if invalid_period_reason:
        return [], invalid_period_reason
    if not _PERIOD_PATTERN.search(normalized):
        return [], "未提供具体上课节次"
    if not _DAY_PATTERN.search(normalized):
        return [], "未识别到星期"
    return [], "无法将星期与具体节次对应"


def build_timetable(courses: list[dict[str, Any]]) -> dict[str, Any]:
    """Build grid entries and retain every ambiguous course below the grid.

    Week ranges and odd/even-week markers are display metadata only. Every
    recognizable weekday plus concrete period range is placed in the grid; a
    course stays below the grid only when no usable pair can be extracted.
    """
    entries: list[dict[str, Any]] = []
    unscheduled: list[dict[str, str]] = []
    scheduled_course_ids: set[str] = set()

    for index, source in enumerate(courses):
        if not isinstance(source, dict):
            continue
        course = _course_display(source)
        course_key = course["teaching_class_id"] or f"course-{index + 1}"
        raw_schedule = str(course["teaching_place"] or "").strip()
        if not raw_schedule:
            unscheduled.append({**course, "reason": "暂未安排上课时间"})
            continue

        parsed_fragments, failure_reason = _parse_schedule(raw_schedule)

        if failure_reason or not parsed_fragments:
            unscheduled.append(
                {
                    **course,
                    "reason": failure_reason or "学校返回了非标准排课格式",
                }
            )
            continue

        scheduled_course_ids.add(course_key)
        for fragment_index, parsed in enumerate(parsed_fragments, start=1):
            entries.append(
                {
                    "id": f"{course_key}:{fragment_index}",
                    "course_id": course_key,
                    "course_name": course["course_name"],
                    "teacher_name": course["teacher_name"],
                    "course_number": course["course_number"],
                    "course_type_name": course["course_type_name"],
                    "campus_name": course["campus_name"],
                    **parsed,
                }
            )

    entries.sort(
        key=lambda item: (
            int(item["day"]),
            int(item["start_period"]),
            int(item["end_period"]),
            str(item["course_name"]),
        )
    )
    return {
        "day_names": list(DAY_NAMES),
        "period_count": MAX_PERIOD,
        "entries": entries,
        "unscheduled": unscheduled,
        "total_count": len(courses),
        "scheduled_count": len(scheduled_course_ids),
        "unscheduled_count": len(unscheduled),
    }


__all__ = ["DAY_NAMES", "MAX_PERIOD", "build_timetable"]
