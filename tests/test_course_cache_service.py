from __future__ import annotations

import json

import config
from services import course_cache_service


def _scope(student="2024110122", batch="batch-a", campus="01"):
    return course_cache_service.CatalogCacheScope(student, batch, campus)


def _payload(name="缓存课程"):
    return {
        "total_count": 1,
        "courses": [{"course_name": name, "tcList": []}],
        "msg": "",
        "is_error": False,
    }


def test_cache_is_exactly_scoped_and_does_not_persist_raw_student_id(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(course_cache_service, "_path", path)
    scope = _scope()

    assert course_cache_service.put_page(scope, "TJKC", 1, 10, _payload()) is True
    cached = course_cache_service.get_page(scope, "TJKC", 1, 10)

    assert cached is not None
    assert cached["cached"] is True
    assert cached["cache_read_only"] is True
    assert cached["courses"][0]["course_name"] == "缓存课程"
    assert scope.student_id not in path.read_text(encoding="utf-8")

    assert course_cache_service.get_page(_scope(student="2024110999"), "TJKC", 1, 10) is None
    assert course_cache_service.get_page(_scope(batch="batch-b"), "TJKC", 1, 10) is None
    assert course_cache_service.get_page(_scope(campus="02"), "TJKC", 1, 10) is None
    assert course_cache_service.get_page(scope, "FANKC", 1, 10) is None
    assert course_cache_service.get_page(scope, "TJKC", 2, 10) is None


def test_empty_or_invalid_page_never_replaces_valid_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "cache.json")
    scope = _scope()
    course_cache_service.put_page(scope, "TJKC", 1, 10, _payload("原课程"))

    assert (
        course_cache_service.put_page(
            scope,
            "TJKC",
            1,
            10,
            {"total_count": 0, "courses": [], "is_error": False},
        )
        is False
    )
    assert (
        course_cache_service.put_page(
            scope,
            "TJKC",
            1,
            10,
            {"total_count": 1, "courses": [{"course_name": "invalid"}]},
        )
        is False
    )
    assert (
        course_cache_service.put_page(
            scope,
            "TJKC",
            1,
            10,
            {"total_count": 1, "courses": "invalid", "is_error": False},
        )
        is False
    )
    assert (
        course_cache_service.put_page(
            scope,
            "TJKC",
            1,
            10,
            {"total_count": True, "courses": [{"course_name": "invalid"}]},
        )
        is False
    )
    assert (
        course_cache_service.get_page(scope, "TJKC", 1, 10)["courses"][0]["course_name"] == "原课程"
    )


def test_stale_cache_is_labeled_and_can_be_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "cache.json")
    monkeypatch.setattr(config, "catalog_cache_ttl_seconds", 300)
    current_time = [1000.0]
    monkeypatch.setattr(course_cache_service.time, "time", lambda: current_time[0])
    scope = _scope()
    course_cache_service.put_page(scope, "TJKC", 1, 10, _payload())

    current_time[0] = 1401.0
    cached = course_cache_service.get_page(scope, "TJKC", 1, 10, allow_stale=True)

    assert cached is not None
    assert cached["cache_stale"] is True
    assert cached["cache_age_seconds"] == 401
    assert (
        course_cache_service.get_page(
            scope,
            "TJKC",
            1,
            10,
            allow_stale=False,
        )
        is None
    )


def test_cache_older_than_hard_limit_is_never_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(course_cache_service, "_path", tmp_path / "cache.json")
    current_time = [1000.0]
    monkeypatch.setattr(course_cache_service.time, "time", lambda: current_time[0])
    scope = _scope()
    assert course_cache_service.put_page(scope, "TJKC", 1, 10, _payload()) is True

    current_time[0] += course_cache_service.MAX_STALE_SECONDS + 1

    assert course_cache_service.get_page(scope, "TJKC", 1, 10) is None


def test_cache_entry_limit_is_strict_and_evicts_oldest(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(course_cache_service, "_path", path)
    monkeypatch.setattr(course_cache_service, "MAX_CACHE_ENTRIES", 2)
    current_time = [1000.0]
    monkeypatch.setattr(course_cache_service.time, "time", lambda: current_time[0])
    scope = _scope()

    for page in range(1, 4):
        assert course_cache_service.put_page(scope, "TJKC", page, 10, _payload()) is True
        current_time[0] += 1

    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 2
    assert course_cache_service.get_page(scope, "TJKC", 1, 10) is None
    assert course_cache_service.get_page(scope, "TJKC", 2, 10) is not None
    assert course_cache_service.get_page(scope, "TJKC", 3, 10) is not None


def test_corrupt_or_old_schema_cache_is_treated_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(course_cache_service, "_path", path)
    path.write_text("{broken", encoding="utf-8")
    assert course_cache_service.get_page(_scope(), "TJKC", 1, 10) is None

    path.write_text(json.dumps({"schema": 1, "entries": {}}), encoding="utf-8")
    assert course_cache_service.get_page(_scope(), "TJKC", 1, 10) is None
