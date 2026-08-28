from __future__ import annotations

import json

import pytest
import requests

import choose_course
import config
import course_list
from course_models import CoursesResponse
from services import backend_service, course_service


class DummyResponse:
    status_code = 200
    text = "mocked"


class DummyCourseResponse:
    status_code = 200
    text = '{"code":"1","dataList":[]}'

    def json(self):
        return {
            "totalCount": 0,
            "dataList": [],
            "msg": "查询推荐选课成功",
            "code": "1",
            "timestamp": "0",
        }


class LoginPageResponse:
    status_code = 200
    text = (
        '<form action="student/check/login.do"><input name="vtoken"><input name="loginPwd"></form>'
    )

    def json(self):
        raise ValueError("not json")

    def raise_for_status(self):
        return None


def test_enrollment_request_contract_is_source_compatible(monkeypatch):
    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return DummyResponse()

    monkeypatch.setattr(choose_course._session, "post", fake_post)
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "2025202601")
    monkeypatch.setattr(config, "combined_cookie", "route=x; JSESSIONID=y")
    monkeypatch.setattr(config, "token", "token")

    response = choose_course.submit_course_selection(
        "202520262010192004801",
        "FANKC",
    )

    assert response.status_code == 200
    assert captured["url"].endswith("elective/volunteer.do")
    assert set(captured["data"]) == {"addParam"}
    assert captured["data"]["addParam"] == (
        '{"data":{"operationType":"1","studentCode":2024110122,'
        '"electiveBatchCode":2025202601,"teachingClassId":202520262010192004801,'
        '"isMajor":"1","campus":"01","teachingClassType":FANKC,'
        '"chooseVolunteer":"1"}}'
    )
    assert captured["headers"]["Cookie"] == "route=x; JSESSIONID=y"
    assert captured["headers"]["token"] == "token"
    assert captured["timeout"] == choose_course.REQUEST_TIMEOUT


def test_withdraw_request_contract_uses_delete_param_and_string_values(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return DummyResponse()

    monkeypatch.setattr(choose_course._session, "post", fake_post)
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "2025202601")
    monkeypatch.setattr(config, "combined_cookie", "route=x; JSESSIONID=y")
    monkeypatch.setattr(config, "token", "token")

    response = choose_course.delete_course_selection("202520262010192004801")

    assert response.status_code == 200
    assert captured["url"].endswith("elective/deleteVolunteer.do")
    assert json.loads(captured["data"]["deleteParam"]) == {
        "data": {
            "operationType": "2",
            "studentCode": "2024110122",
            "electiveBatchCode": "2025202601",
            "teachingClassId": "202520262010192004801",
            "isMajor": "1",
        }
    }
    assert captured["timeout"] == choose_course.REQUEST_TIMEOUT


def test_recommended_course_uses_zero_based_school_page_and_dedicated_endpoint(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return DummyCourseResponse()

    monkeypatch.setattr(course_list.requests, "post", fake_post)
    monkeypatch.setattr(config, "student_id", "2024110122")
    monkeypatch.setattr(config, "elective_batch_code", "batch")
    monkeypatch.setattr(config, "combined_cookie", "cookie")
    monkeypatch.setattr(config, "token", "token")

    result = course_list.recommended_course(0)
    query = json.loads(captured["data"]["querySetting"])

    assert isinstance(result, CoursesResponse)
    assert captured["url"].endswith("elective/recommendedCourse.do")
    assert query["pageNumber"] == 0
    assert query["data"]["teachingClassType"] == "TJKC"
    assert captured["timeout"] == course_list.REQUEST_TIMEOUT


def test_course_service_rejects_negative_school_page():
    success, message, _ = course_service.query_courses("TJKC", -1)
    assert not success
    assert "大于等于 0" in message


def test_course_service_identifies_closed_course_window(monkeypatch):
    response = CoursesResponse(
        total_count=0,
        data_list=[],
        msg="当前非选课时间，课程目录未开放",
        code="0",
        timestamp="0",
    )
    monkeypatch.setitem(
        course_service.COURSE_TYPE_MAP,
        "TJKC",
        ("本班课程(推荐)", lambda _page: response),
    )

    success, error_code, _ = course_service.query_courses("TJKC", 0)

    assert success is False
    assert error_code == course_service.COURSE_WINDOW_CLOSED


def test_phase_whitelist_matches_supported_enrollment_stages():
    assert config.is_automatic_enroll_phase("复选阶段")
    assert config.is_automatic_enroll_phase("正选")
    assert config.is_automatic_enroll_phase("补选阶段")
    assert config.is_automatic_enroll_phase("补退选")
    assert not config.is_automatic_enroll_phase("预选阶段")
    assert not config.is_automatic_enroll_phase("")


@pytest.mark.parametrize(
    "batch_name",
    (
        "补选尚未开放",
        "复选未开始",
        "正选暂停",
        "补选已结束",
        "补退选截止",
        "系统维护",
    ),
)
def test_closed_phase_keywords_override_automatic_keywords(batch_name):
    assert config.classify_elective_phase(batch_name) == config.PHASE_CLOSED
    assert not config.is_automatic_enroll_phase(batch_name)
    assert "未启动" in config.automatic_enroll_block_reason(batch_name)


def test_selected_course_login_page_is_not_treated_as_empty(monkeypatch):
    monkeypatch.setattr(
        choose_course._session,
        "post",
        lambda **kwargs: LoginPageResponse(),
    )

    with pytest.raises(choose_course.SchoolSessionExpiredError):
        choose_course.query_enrolled_courses("cookie", "token")


def test_backend_headers_replace_host_origin_and_referer():
    profile = backend_service.get_profile("webvpn")
    headers = backend_service.build_headers(profile, token="token")
    assert headers["Host"] == "bkxk.webvpn.szu.edu.cn"
    assert headers["Origin"] == "https://bkxk.webvpn.szu.edu.cn"
    assert headers["Referer"].startswith("https://bkxk.webvpn.szu.edu.cn/")


def test_webvpn_headers_do_not_include_authserver_cookies(monkeypatch):
    monkeypatch.setattr(config, "combined_cookie", "route=school")
    monkeypatch.setattr(config, "webvpn_cookie", "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig")
    monkeypatch.setattr(config, "authserver_cookie", "route=auth; JSESSIONID=auth-session")

    headers = backend_service.build_headers(
        backend_service.get_profile(config.BACKEND_WEBVPN),
    )

    assert "route=auth" not in headers["Cookie"]
    assert "auth-session" not in headers["Cookie"]


def test_auto_backend_fails_over_on_gateway_status(monkeypatch):
    monkeypatch.setattr(config, "backend_preference", config.BACKEND_AUTO)
    monkeypatch.setattr(config, "webvpn_cookie", "_webvpn_key=k; webvpn_username=u; webvpn_username_NS_Sig=s")
    calls = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    def sender(**kwargs):
        calls.append(kwargs)
        return Response(503 if len(calls) == 1 else 200)

    result = backend_service.request_with_failover("GET", "student/1.do", sender=sender)
    assert result.status_code == 200
    assert calls[0]["headers"]["Host"] == "bkxk.szu.edu.cn"
    assert calls[1]["headers"]["Host"] == "bkxk.webvpn.szu.edu.cn"
    assert "_webvpn_key=k" in calls[1]["headers"]["Cookie"]


def test_webvpn_cookie_set_isolated_from_primary(monkeypatch):
    monkeypatch.setattr(config, "webvpn_cookie", "")
    monkeypatch.setattr(config, "combined_cookie", "route=old; JSESSIONID=old-session")
    assert backend_service.merge_set_cookie(
        [
            "route=new-route; Path=/, JSESSIONID=new-session; Path=/, "
            "_webvpn_key=k; Path=/, webvpn_username=u; Path=/, webvpn_username_NS_Sig=s; Path=/"
        ],
        backend_service.WEBVPN_HOST,
    )
    assert backend_service.has_webvpn_cookies()
    assert "route=new-route" in config.combined_cookie
    assert "JSESSIONID=new-session" in config.combined_cookie
    assert "_webvpn_key=k" not in backend_service.cookie_header(backend_service.get_profile("primary"))
    assert "_webvpn_key=k" in backend_service.cookie_header(backend_service.get_profile("webvpn"))
    assert "route=new-route" in backend_service.cookie_header(backend_service.get_profile("webvpn"))


def test_webvpn_set_cookie_keeps_unknown_school_cookies(monkeypatch):
    monkeypatch.setattr(config, "combined_cookie", "route=route")
    monkeypatch.setattr(config, "webvpn_cookie", "")

    assert backend_service.merge_set_cookie(
        [
            "course_extra=extra; Path=/xsxkapp/, "
            "_webvpn_key=key; Path=/, webvpn_username=user; Path=/, "
            "webvpn_username_NS_Sig=sig; Path=/"
        ],
        backend_service.WEBVPN_HOST,
    )

    assert "course_extra=extra" in config.combined_cookie
    assert "course_extra=extra" in backend_service.cookie_header(
        backend_service.get_profile(config.BACKEND_WEBVPN)
    )
    assert "course_extra=extra" not in config.webvpn_cookie


def test_auto_cooldown_skips_primary_until_cleared(monkeypatch):
    backend_service.clear_primary_cooldown()
    monkeypatch.setattr(config, "backend_preference", config.BACKEND_AUTO)
    monkeypatch.setattr(config, "combined_cookie", "route=route; JSESSIONID=session")
    monkeypatch.setattr(
        config,
        "webvpn_cookie",
        "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
    )
    calls = []

    def sender(**kwargs):
        calls.append(kwargs["url"])
        if "bkxk.szu.edu.cn" in kwargs["url"]:
            raise requests.ConnectionError("primary unavailable")
        return DummyResponse()

    backend_service.request_with_failover("GET", "status", sender=sender, preference="auto")
    assert ["bkxk.szu.edu.cn" in url for url in calls] == [True, False]
    calls.clear()

    backend_service.request_with_failover("GET", "status", sender=sender, preference="auto")
    assert calls == ["https://bkxk.webvpn.szu.edu.cn/xsxkapp/sys/xsxkapp/status"]
    payload = backend_service.backend_payload()
    assert payload["auto_fallback_active"] is True
    assert payload["primary_cooldown_remaining"] > 0

    backend_service.clear_primary_cooldown()
