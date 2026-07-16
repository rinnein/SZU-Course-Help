from __future__ import annotations

import pytest

import config
import logic
from school_password import encrypt_school_password
from services import auth_service


def test_login_parameter_validation_requires_exact_four_valid_points():
    valid = [[10, 30], [40, 40], [80, 50], [120, 60]]
    args = ("2024110122", "password", "SZU3.payload.signature")

    assert auth_service.validate_login_params(*args, valid, "token", "route=x") is None
    assert auth_service.validate_login_params(*args, valid[:3], "token", "route=x")
    assert auth_service.validate_login_params(*args, valid + [[1, 1]], "token", "route=x")
    assert auth_service.validate_login_params(*args, [[-1, 2], *valid[1:]], "token", "route=x")
    assert auth_service.validate_login_params("abc", "password", args[2], valid, "token", "route=x")


def test_cookie_parsing_handles_expires_commas():
    raw = (
        "route=abc; Path=/, insert_cookie=xyz; Expires=Wed, 21 Oct 2030 07:28:00 GMT; Path=/, "
        "JSESSIONID=session; Path=/, _WEU=weu-token; Path=/"
    )
    assert logic.parse_cookie(raw) == "route=abc; insert_cookie=xyz"
    assert logic.parse_login_cookie(raw) == "JSESSIONID=session; _WEU=weu-token"


def test_coordinate_serialization_rejects_invalid_values():
    serialize = logic.serialize_captcha_coordinates
    assert serialize([[1, 2], [3, 4], [5, 6], [7, 8]]) == "1-2,3-4,5-6,7-8"
    assert serialize([[1, 2]]) == ""
    assert serialize([[1, 2], [3, 4], [5, 6], [999, 8]]) == ""


def test_ocr_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "password", "secret")
    monkeypatch.setattr(logic, "get_new_image", lambda: ("token", "route=a; insert_cookie=b"))
    monkeypatch.setattr(logic, "recognize_captcha_centers", lambda: [])
    monkeypatch.setattr(logic.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="连续 2 次"):
        logic.verify_vcode(max_attempts=2)


def test_ocr_relogin_default_attempt_limit_is_fifty():
    assert config.ocr_relogin_max_attempts == 50
    assert logic.verify_vcode.__defaults__ == (50,)
    assert auth_service.attempt_ocr_relogin.__defaults__ == (50,)
    assert auth_service.attempt_automatic_relogin.__defaults__ == (50,)


def test_school_password_protocol_matches_known_vectors():
    assert encrypt_school_password("school-password") == (
        "ODFBNjdGNENFMDkyOUNGMTI3OTkxOTFBRjU4NUI1M0RFNENCNDAwMTdCQjJBNkMwOTNDMjk4RjMxNzQyRjY2Nw=="
    )
    assert encrypt_school_password("P@ssw0rd!") == (
        "NkQ0MEQ5MUMwN0IwRjJFQTkxNkQzRUVFMzAwMERFNTg4MDlCQzU2QjU3Q0Y5QzMx"
    )


def test_ocr_retries_transient_exception_before_success(monkeypatch):
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "password", "secret")
    calls = []

    def fake_image():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary malformed image")
        return "token", "route=a; insert_cookie=b"

    monkeypatch.setattr(logic, "get_new_image", fake_image)
    monkeypatch.setattr(logic.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        logic,
        "recognize_captcha_centers",
        lambda: [[1, 2], [3, 4], [5, 6], [7, 8]],
    )

    result = logic.verify_vcode(max_attempts=2)
    assert len(calls) == 2
    assert result[0] == "token"
    assert result[3] == "1-2,3-4,5-6,7-8"


def test_batch_refresh_discards_result_from_replaced_session(monkeypatch):
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "token", "old-token")
    monkeypatch.setattr(config, "combined_cookie", "old-cookie")
    monkeypatch.setattr(config, "elective_batch_code", "")
    monkeypatch.setattr(config, "elective_batch_name", "")

    def replace_session(*_args):
        config.token = "new-token"
        config.combined_cookie = "new-cookie"
        return "stale-code", "复选阶段"

    monkeypatch.setattr(logic, "fetch_elective_batch", replace_session)

    with pytest.raises(RuntimeError, match="丢弃过期批次结果"):
        auth_service.refresh_elective_batch("2024110122", "old-token")

    assert config.elective_batch_code == ""
    assert config.elective_batch_name == ""


def test_captcha_fetch_retries_transient_failure(monkeypatch):
    calls = []

    def fake_fetch():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("empty image")
        return {"vtoken": "token", "cookie": "route=a", "imageUrl": "data:image/jpeg;base64,x"}

    monkeypatch.setattr(logic, "_fetch_vtoken_and_image_once", fake_fetch)
    monkeypatch.setattr(logic.time, "sleep", lambda *_: None)

    result = logic.fetch_vtoken_and_image(max_attempts=3)
    assert result["vtoken"] == "token"
    assert len(calls) == 2


def test_automatic_relogin_updates_runtime_state(monkeypatch):
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "password", "secret")
    monkeypatch.setattr(
        auth_service,
        "attempt_ocr_relogin",
        lambda max_attempts=config.ocr_relogin_max_attempts: (
            "vtoken",
            "route=a",
            "encrypted",
            "1-2,3-4,5-6,7-8",
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "perform_school_login",
        lambda *args: {
            "success": True,
            "cookie": "JSESSIONID=s",
            "name": "Tester",
            "token": "new-token",
        },
    )
    monkeypatch.setattr(auth_service, "refresh_elective_batch", lambda *args: None)

    success, error = auth_service.attempt_automatic_relogin(max_attempts=1)
    assert success and error == ""
    assert config.combined_cookie == "JSESSIONID=s; route=a"


def test_failed_automatic_relogin_invalidates_school_session(monkeypatch):
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "password", "secret")
    monkeypatch.setattr(config, "token", "expired-token")
    monkeypatch.setattr(config, "combined_cookie", "expired-cookie")
    monkeypatch.setattr(
        auth_service,
        "attempt_ocr_relogin",
        lambda max_attempts=config.ocr_relogin_max_attempts: (_ for _ in ()).throw(
            RuntimeError("ocr failed")
        ),
    )

    success, error = auth_service.attempt_automatic_relogin(max_attempts=1)
    assert not success and "ocr failed" in error
    assert config.token == ""
    assert config.combined_cookie == ""
    assert config.student_id == "2024110122"
    assert config.password == "secret"
