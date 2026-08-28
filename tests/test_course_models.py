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
        assert all(
            class_info.teaching_class_id
            for course in frontend.courses
            for class_info in course.teaching_classes
        )


def test_course_projection_preserves_summary_metadata():
    response = CoursesResponse.from_json((FIXTURE_DIR / "1.json").read_text(encoding="utf-8"))
    course = response.to_course_list_response().courses[0].to_api_dict()

    assert course["course_number"] == "9900500002"
    assert course["course_type_name"] == "公共选修课"
    assert course["course_nature_name"] == "选修"
    assert course["department_name"] == "人文学院"
    assert course["credit"] == "2"
