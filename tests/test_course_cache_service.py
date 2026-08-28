from __future__ import annotations

import json

from services import course_cache_service


def _payload(*courses: dict) -> dict:
    return {
        "total_count": len(courses),
        "courses": list(courses),
        "msg": "",
        "is_error": False,
    }


def test_non_empty_course_response_is_persisted_with_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "courses.json")

    payload = _payload({"course_name": "缓存课程"})
    assert course_cache_service.put("TJKC", 1, 10, payload) is True

    cached = course_cache_service.get("TJKC", 1, 10)
    assert cached is not None
    assert cached["courses"] == payload["courses"]
    assert cached["cached"] is True
    assert cached["has_cache"] is True
    assert cached["cached_at"] > 0
    assert cached["cache_version"] == 1


def test_empty_or_invalid_response_does_not_replace_existing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "courses.json")
    original = _payload({"course_name": "旧课程"})
    course_cache_service.put("TJKC", 1, 10, original)

    assert course_cache_service.put("TJKC", 1, 10, {"courses": []}) is False
    assert course_cache_service.put("TJKC", 1, 10, {"courses": "invalid"}) is False
    assert course_cache_service.get("TJKC", 1, 10)["courses"] == original["courses"]


def test_corrupt_cache_file_is_treated_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "courses.json"
    monkeypatch.setattr(course_cache_service, "_path", path)
    path.write_text("{not-json", encoding="utf-8")
    assert course_cache_service.get("TJKC", 1, 10) is None

    path.write_text(json.dumps({"entries": []}), encoding="utf-8")
    assert course_cache_service.get("TJKC", 1, 10) is None


def test_full_catalog_cache_ignores_page_and_is_limited_to_catalog_types(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "courses.json")
    payload = _payload(
        {"course_name": "第一页课程"},
        {"course_name": "第二页课程"},
    )

    assert course_cache_service.put_full("TJKC", payload) is True
    cached = course_cache_service.get_full("TJKC")
    assert cached is not None
    assert cached["courses"] == payload["courses"]
    assert cached["full_catalog"] is True
    assert course_cache_service.get_full("FAWKC") is None


def test_empty_full_catalog_does_not_replace_existing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "courses.json")
    original = _payload({"course_name": "完整课程"})
    course_cache_service.put_full("FANKC", original)

    assert course_cache_service.put_full("FANKC", {"courses": []}) is False
    assert course_cache_service.get_full("FANKC")["courses"] == original["courses"]
