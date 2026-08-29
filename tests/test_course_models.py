from __future__ import annotations

from pathlib import Path

from course_models import CoursesResponse

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def test_captured_school_course_responses_parse_into_frontend_models():
    for filename in ("1.json", "2.json"):
        response = CoursesResponse.from_json((FIXTURE_DIR / filename).read_text(encoding="utf-8"))
        frontend = response.to_course_list_response()

        assert str(response.code) == "1"
        assert not frontend.is_error
        assert frontend.total_count >= len(frontend.courses)
        assert all(course.course_name for course in frontend.courses)
        assert all(hasattr(course, "credit") for course in frontend.courses)
        assert all(hasattr(course, "course_type_name") for course in frontend.courses)
        assert all(hasattr(course, "course_nature_name") for course in frontend.courses)
        assert all(
            class_info.teaching_class_id
            for course in frontend.courses
            for class_info in course.teaching_classes
        )
        assert all(
            hasattr(class_info, "course_number")
            for course in frontend.courses
            for class_info in course.teaching_classes
        )
