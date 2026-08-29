"""Tests for the shared-session reverse proxy to ``bkxk.szu.edu.cn``.

Everything here runs fully offline: ``conftest.py`` blocks real ``requests``
and ``httpx`` network traffic, and the proxy's upstream client is replaced by
an in-memory fake so no school system is ever contacted.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.responses import Response

import config
from services import auth_service, proxy_service


def set_logged_session(monkeypatch, *, cookie="route=a; JSESSIONID=b; _WEU=c", token="tok"):
    monkeypatch.setattr(config, "combined_cookie", cookie)
    monkeypatch.setattr(config, "token", token)
    monkeypatch.setattr(config, "student_id", "2024110122")


def set_logged_out(monkeypatch):
    monkeypatch.setattr(config, "combined_cookie", "")
    monkeypatch.setattr(config, "token", "")
    monkeypatch.setattr(config, "student_id", "")


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------


class TestLinkRewrite:
    def test_absolute_school_http_rewritten(self):
        ref = "http://bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
        assert proxy_service.rewrite_link_ref(ref) == (
            "/proxy/bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
        )

    def test_protocol_relative_rewritten(self):
        assert proxy_service.rewrite_link_ref("//bkxk.szu.edu.cn/foo.js") == (
            "/proxy/bkxk.szu.edu.cn/foo.js"
        )

    def test_absolute_webvpn_redirect_with_port_is_rewritten(self):
        ref = "https://webvpn.szu.edu.cn:443/vpn_key/update?origin=x"
        assert proxy_service.rewrite_link_ref(ref) == (
            "/proxy/webvpn.szu.edu.cn/vpn_key/update?origin=x"
        )

    def test_other_host_untouched(self):
        assert proxy_service.rewrite_link_ref("http://example.com/x") == ("http://example.com/x")

    def test_root_relative_redirect_is_rewritten(self):
        assert proxy_service.rewrite_proxy_path("/xsxkapp/login.do") == (
            "/proxy/bkxk.szu.edu.cn/xsxkapp/login.do"
        )

    def test_html_root_relative_attributes_rewritten(self):
        html = (
            '<link rel="stylesheet" href="/xsxkapp/om/css/app.css">'
            '<img src="/xsxkapp/om/i.png"><a href="/api/session">x</a>'
        )
        out = proxy_service.rewrite_html(html)
        assert "/proxy/bkxk.szu.edu.cn/xsxkapp/om/css/app.css" in out
        assert "/proxy/bkxk.szu.edu.cn/xsxkapp/om/i.png" in out
        # Our own API paths are never rewritten.
        assert 'href="/api/session"' in out

    def test_text_body_passthrough_for_binary(self):
        raw = b"\x89PNG\r\n\x1a\nbinary"
        assert proxy_service.rewrite_text_body(raw, "image/png") is raw

    def test_text_body_rewrites_html(self):
        body = b'<a href="http://bkxk.szu.edu.cn/x">y</a>'
        out = proxy_service.rewrite_text_body(body, "text/html; charset=utf-8")
        assert b"/proxy/bkxk.szu.edu.cn/x" in out

    def test_webvpn_text_rewrites_primary_absolute_links_to_webvpn_proxy(self):
        body = b'<script src="http://bkxk.szu.edu.cn/xsxkapp/app.js"></script>'
        out = proxy_service.rewrite_text_body(
            body,
            "text/html; charset=utf-8",
            proxy_service.backend_service.WEBVPN_HOST,
        )
        assert b"/proxy/bkxk.webvpn.szu.edu.cn/xsxkapp/app.js" in out

    def test_authserver_script_root_paths_are_rewritten(self):
        body = (
            b'$.ajax({url: contextPath + "/qrCode/getToken"});'
            b'var image = "/authserver/qrCode/getCode?uuid=x";'
        )
        out = proxy_service.rewrite_text_body(
            body,
            "application/javascript; charset=utf-8",
            proxy_service.backend_service.AUTHSERVER_HOST,
        )
        assert b"/proxy/authserver-443.webvpn.szu.edu.cn/authserver/qrCode/getToken" in out
        assert b"/proxy/authserver-443.webvpn.szu.edu.cn/authserver/qrCode/getCode?uuid=x" in out

    def test_html_bootstraps_shared_browser_session(self):
        out = proxy_service.inject_shared_session_bootstrap(
            "<html><head><title>x</title></head></html>", 'tok"en', "2024110122"
        )
        assert "sessionStorage.setItem('token'" in out
        assert 'tok\\"en' in out
        assert "studentInfo" in out
        assert "currentBatch" in out
        assert "currentCampus" in out
        assert "sysParam" in out
        assert "dictionary" in out
        assert "/sys/xsxkapp/student/" in out
        assert "/sys/xsxkapp/publicinfo/sysparam.do" in out
        assert "/sys/xsxkapp/publicinfo/dictionary.do" in out

    def test_school_entry_path_is_the_original_main_page(self):
        assert proxy_service.SCHOOL_ENTRY_URL == (
            "http://bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
        )


# ---------------------------------------------------------------------------
# Upstream header composition
# ---------------------------------------------------------------------------


class TestUpstreamHeaders:
    def test_forces_host_origin_referer_cookie_token_ua(self):
        headers = proxy_service.build_upstream_headers(
            {"Cookie": "bogus-jsessionid=1"}, "route=a; JSESSIONID=b", "tok"
        )
        assert headers["Host"] == "bkxk.szu.edu.cn"
        assert headers["Origin"] == "http://bkxk.szu.edu.cn"
        assert headers["Referer"].startswith("http://bkxk.szu.edu.cn/")
        assert headers["Cookie"] == "route=a; JSESSIONID=b"
        assert headers["token"] == "tok"
        assert headers["X-Requested-With"] == "XMLHttpRequest"
        assert "bogus-jsessionid" not in headers["Cookie"]

    def test_proxy_cookie_mirror_is_path_scoped_and_keeps_backend_cookies(self, monkeypatch):
        set_logged_session(
            monkeypatch,
            cookie="route=route1; insert_cookie=insert1; JSESSIONID=session1; _WEU=weu1",
        )
        monkeypatch.setattr(
            config,
            "webvpn_cookie",
            "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
        )
        monkeypatch.setattr(
            config,
            "combined_cookie",
            "route=route1; insert_cookie=insert1; JSESSIONID=session1; _WEU=weu1",
        )

        headers = proxy_service.proxy_cookie_headers(proxy_service.backend_service.WEBVPN_HOST)

        assert len(headers) == 7
        assert all("Path=/proxy/bkxk.webvpn.szu.edu.cn/" in value for value in headers)
        assert any(value.startswith("_webvpn_key=key;") for value in headers)
        assert any(value.startswith("JSESSIONID=session1;") for value in headers)
        assert all("Domain=" not in value for value in headers)

    def test_primary_proxy_cookie_mirror_excludes_webvpn_cookies(self, monkeypatch):
        set_logged_session(monkeypatch)
        monkeypatch.setattr(
            config,
            "webvpn_cookie",
            "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
        )

        headers = proxy_service.proxy_cookie_headers(proxy_service.SCHOOL_HOST)

        assert {value.split("=", 1)[0] for value in headers} == {
            "route",
            "JSESSIONID",
            "_WEU",
        }
        assert all("Path=/proxy/bkxk.szu.edu.cn/" in value for value in headers)
        assert all("webvpn" not in value.lower() for value in headers)

    def test_clear_proxy_cookie_mirror_expires_both_namespaces(self):
        response = proxy_service.clear_proxy_cookie_mirror(Response(content=b"ok"))
        headers = [
            value.decode("latin-1")
            for name, value in response.raw_headers
            if name.lower() == b"set-cookie"
        ]

        assert any("_webvpn_key=;" in value for value in headers)
        assert any("Path=/proxy/bkxk.webvpn.szu.edu.cn/" in value for value in headers)
        assert any("Path=/proxy/bkxk.szu.edu.cn/" in value for value in headers)

    def test_forwarded_content_type_and_custom_x_headers(self):
        headers = proxy_service.build_upstream_headers(
            {"Content-Type": "application/x-www-form-urlencoded", "x-app-hint": "1"},
            "cookie",
            "tok",
        )
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert headers["x-app-hint"] == "1"

    def test_host_never_overridable_by_client(self):
        headers = proxy_service.build_upstream_headers({"Host": "evil.example"}, "c", "t")
        assert headers["Host"] == "bkxk.szu.edu.cn"


# ---------------------------------------------------------------------------
# Session sharing between API mode and proxy mode
# ---------------------------------------------------------------------------


class TestSessionSharing:
    def test_get_shared_session_reflects_config(self, monkeypatch):
        set_logged_session(monkeypatch)
        logged_in, cookie, token = auth_service.get_shared_session()
        assert logged_in is True
        assert cookie == "route=a; JSESSIONID=b; _WEU=c"
        assert token == "tok"

    def test_get_shared_session_when_logged_out(self, monkeypatch):
        set_logged_out(monkeypatch)
        logged_in, cookie, token = auth_service.get_shared_session()
        assert logged_in is False
        assert cookie == ""
        assert token == ""

    def test_merge_session_cookies_updates_shared_state(self, monkeypatch):
        config.combined_cookie = "route=old; JSESSIONID=oldid; _WEU=oldweu"
        config.token = "tok"
        monkeypatch.setattr(config, "combined_cookie", config.combined_cookie)
        monkeypatch.setattr(config, "token", "tok")
        ok = auth_service.merge_session_cookies(
            "JSESSIONID=newid; Path=/; HttpOnly, _WEU=newweu; Path=/; HttpOnly"
        )
        assert ok is True
        assert "JSESSIONID=newid" in config.combined_cookie
        assert "_WEU=newweu" in config.combined_cookie
        assert "route=old" in config.combined_cookie
        # token/session untouched by cookie rotation.
        assert config.token == "tok"

    def test_merge_session_cookies_noop_when_no_session(self, monkeypatch):
        monkeypatch.setattr(config, "combined_cookie", "")
        assert auth_service.merge_session_cookies("JSESSIONID=x; Path=/") is False

    def test_merge_session_cookies_ignores_garbage(self, monkeypatch):
        monkeypatch.setattr(config, "combined_cookie", "route=a")
        assert auth_service.merge_session_cookies("garbage-without-equals") is False


# ---------------------------------------------------------------------------
# Proxy request handling (offline fake client)
# ---------------------------------------------------------------------------


class SimpleHeaders:
    def __init__(self, data):
        self._data = {k.lower(): str(v) for k, v in data.items()}

    def get(self, key, default=None):
        return self._data.get(key.lower(), default)

    def get_list(self, key):
        val = self._data.get(key.lower())
        return [val] if val is not None else []

    def items(self):
        return list(self._data.items())


class FakeQueryParams:
    def __init__(self, *items):
        self._items = items

    def multi_items(self):
        return list(self._items)


class FakeResponse:
    """Minimal stand-in for ``httpx.Response`` used by ``proxy_request``."""

    def __init__(self, status_code=200, headers=None, text="", body=None):
        self.status_code = status_code
        self.headers = SimpleHeaders(headers or {})
        self._body = body if body is not None else text.encode("utf-8")
        self.closed = False
        self.history = []

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for chunk in self._body:
            yield bytes([chunk])

    async def aclose(self):
        self.closed = True
        return None


class FakeAsyncClient:
    def __init__(self, response):
        self._response = response
        self.sent = {}
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, content=None):
        self.sent = {"method": method, "url": url, "headers": headers or {}, "content": content}
        return self.sent

    async def send(self, built_request, stream=False, follow_redirects=False):
        assert built_request is self.sent
        assert stream is True
        assert follow_redirects is True
        return self._response

    async def aclose(self):
        self.closed = True


class RetryingAsyncClient(FakeAsyncClient):
    def __init__(self, response, attempts):
        super().__init__(response)
        self.attempts = attempts

    async def send(self, built_request, stream=False, follow_redirects=False):
        self.attempts.append(built_request)
        if len(self.attempts) == 1:
            raise proxy_service.httpx.RemoteProtocolError("connection closed")
        assert built_request is self.sent
        assert stream is True
        return self._response


class WebVPNFakeAsyncClient(FakeAsyncClient):
    async def send(self, built_request, stream=False, follow_redirects=False):
        assert built_request is self.sent
        assert stream is True
        assert follow_redirects is False
        return self._response


class FakeRequest:
    """Minimal starlette Request stand-in with the fields proxy_request reads."""

    method = "GET"
    headers = {}
    query_params = FakeQueryParams()

    async def body(self):
        return b""


def monkeypatch_proxy_client(monkeypatch, fake_response):
    client = FakeAsyncClient(fake_response)
    monkeypatch.setattr(proxy_service.httpx, "AsyncClient", lambda *a, **k: client)
    return client


async def consume_stream(response):
    return b"".join([chunk async for chunk in response.body_iterator])


class TestProxyRequest:
    def test_unauthenticated_returns_401(self, monkeypatch):
        set_logged_out(monkeypatch)
        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "xsxkapp/"))
        assert resp.status_code == 401
        assert resp.body.decode().startswith("{")

    def test_expired_session_returns_401_and_not_reflected(self, monkeypatch):
        set_logged_session(monkeypatch)
        login_page = (
            '<html><form action="student/check/login.do">'
            '<input name="vtoken"><input name="loginPwd"></form></html>'
        )
        fake = FakeResponse(status_code=200, headers={"Content-Type": "text/html"}, text=login_page)
        monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "elective/volunteer.do"))
        assert resp.status_code == 401
        # Never reflect the school login page body back to the browser.
        assert "loginPwd" not in resp.body.decode()

    @pytest.mark.parametrize("status_code", [302, 401, 403])
    def test_expired_statuses_return_proxy_session_error(self, monkeypatch, status_code):
        set_logged_session(monkeypatch)
        fake = FakeResponse(
            status_code=status_code,
            headers={
                "Content-Type": "text/plain",
                **({} if status_code != 302 else {"Location": ""}),
            },
            text="session rejected",
        )
        monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "expired"))

        assert resp.status_code == 401
        assert resp.body.decode("utf-8")
        assert "PROXY_SESSION_EXPIRED" in resp.body.decode("utf-8")

    def test_successful_proxy_rewrites_html(self, monkeypatch):
        set_logged_session(monkeypatch)
        fake = FakeResponse(
            status_code=200,
            headers={"Content-Type": "text/html"},
            text='<a href="http://bkxk.szu.edu.cn/xsxkapp/om/css/app.css">x</a>',
        )
        client = monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "xsxkapp/"))
        assert resp.status_code == 200
        assert "/proxy/bkxk.szu.edu.cn/xsxkapp/om/css/app.css" in resp.body.decode()
        # Upstream URL maps onto the school root, not the xsxkapp API base.
        assert client.sent["url"] == "http://bkxk.szu.edu.cn/xsxkapp/"
        # Shared session is injected into the forwarded request.
        assert "route=a" in client.sent["headers"]["Cookie"]

    def test_webvpn_remote_protocol_error_retries_on_fresh_connection(self, monkeypatch):
        set_logged_session(monkeypatch)
        monkeypatch.setattr(
            config,
            "webvpn_cookie",
            "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
        )
        clients = []
        attempts = []

        def client_factory(*args, **kwargs):
            client = RetryingAsyncClient(FakeResponse(text="ok"), attempts)
            clients.append(client)
            return client

        monkeypatch.setattr(proxy_service.httpx, "AsyncClient", client_factory)
        response = asyncio.run(
            proxy_service.proxy_request(
                FakeRequest(),
                "xsxkapp/sys/xsxkapp/*default/index.do",
                proxy_service.backend_service.WEBVPN_HOST,
            )
        )

        assert response.status_code == 200
        assert len(clients) == 2
        assert clients[0].closed is True
        assert "_webvpn_key=key" in clients[1].sent["headers"]["Cookie"]
        assert clients[1].sent["url"].startswith("https://bkxk.webvpn.szu.edu.cn/")
        assert clients[1].sent["headers"]["Origin"] == "https://bkxk.webvpn.szu.edu.cn"
        assert clients[1].sent["headers"]["Referer"].startswith("https://bkxk.webvpn.szu.edu.cn/")

    def test_webvpn_proxy_sends_school_and_webvpn_cookies_and_mirrors_them(self, monkeypatch):
        set_logged_session(
            monkeypatch,
            cookie="route=route1; insert_cookie=insert1; JSESSIONID=session1; _WEU=weu1",
        )
        monkeypatch.setattr(
            config,
            "webvpn_cookie",
            "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
        )
        fake = FakeResponse(headers={"Content-Type": "text/plain"}, text="ok")
        client = WebVPNFakeAsyncClient(fake)
        monkeypatch.setattr(proxy_service.httpx, "AsyncClient", lambda *a, **k: client)

        response = asyncio.run(
            proxy_service.proxy_request(
                FakeRequest(),
                "xsxkapp/sys/xsxkapp/*default/index.do",
                proxy_service.backend_service.WEBVPN_HOST,
            )
        )

        sent_cookie = client.sent["headers"]["Cookie"]
        assert "route=route1" in sent_cookie
        assert "JSESSIONID=session1" in sent_cookie
        assert "_webvpn_key=key" in sent_cookie
        assert "webvpn_username=user" in sent_cookie
        assert "webvpn_username_NS_Sig=sig" in sent_cookie
        mirrored = [
            value.decode("latin-1")
            for name, value in response.raw_headers
            if name.lower() == b"set-cookie"
        ]
        assert len(mirrored) == 7
        assert all("Path=/proxy/bkxk.webvpn.szu.edu.cn/" in value for value in mirrored)

    def test_auto_proxy_uses_actual_successful_backend_for_rewrite_and_cookies(self, monkeypatch):
        set_logged_session(monkeypatch)
        monkeypatch.setattr(
            config,
            "backend_preference",
            config.BACKEND_AUTO,
        )
        monkeypatch.setattr(
            config,
            "webvpn_cookie",
            "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
        )
        fake = FakeResponse(
            headers={"Content-Type": "text/html"},
            text='<a href="http://bkxk.szu.edu.cn/x">x</a>',
        )
        client = WebVPNFakeAsyncClient(fake)
        monkeypatch.setattr(proxy_service.httpx, "AsyncClient", lambda *a, **k: client)

        response = asyncio.run(proxy_service.proxy_request(FakeRequest(), "x", "auto"))

        assert "/proxy/bkxk.szu.edu.cn/x" in response.body.decode()
        assert any(
            "Path=/proxy/bkxk.szu.edu.cn/" in value.decode("latin-1")
            for name, value in response.raw_headers
            if name == b"set-cookie"
        )

    def test_query_token_is_replaced_with_shared_token(self, monkeypatch):
        set_logged_session(monkeypatch, token="shared-token")
        fake = FakeResponse(headers={"Content-Type": "text/plain"}, text="ok")
        client = monkeypatch_proxy_client(monkeypatch, fake)
        request = FakeRequest()
        request.query_params = FakeQueryParams(("token", "stale-token"), ("timestamp", "1"))

        asyncio.run(proxy_service.proxy_request(request, "api.do"))

        assert client.sent["url"].endswith("?token=shared-token&timestamp=1")

    def test_root_path_is_forwarded_to_school_root(self, monkeypatch):
        set_logged_session(monkeypatch)
        fake = FakeResponse(
            headers={"Content-Type": "text/html"},
            text='<a href="/xsxkapp/sys/xsxkapp/*default/index.do">进入</a>',
        )
        client = monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), ""))

        assert resp.status_code == 200
        assert client.sent["url"] == "http://bkxk.szu.edu.cn/"
        assert "/proxy/bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do" in (
            resp.body.decode()
        )

    def test_location_header_stays_inside_proxy(self, monkeypatch):
        set_logged_session(monkeypatch)
        fake = FakeResponse(
            status_code=301,
            headers={"Location": "/xsxkapp/sys/xsxkapp/*default/index.do"},
        )
        client = monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "redirect"))

        assert resp.status_code == 301
        assert resp.headers["location"] == (
            "/proxy/bkxk.szu.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
        )
        assert client.sent["url"] == "http://bkxk.szu.edu.cn/redirect"

    def test_binary_passthrough_uses_streaming(self, monkeypatch):
        set_logged_session(monkeypatch)
        fake = FakeResponse(
            status_code=200,
            headers={"Content-Type": "image/png"},
            body=b"\x89PNGdata",
        )
        client = monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "favicon.ico"))
        assert resp.status_code == 200
        assert asyncio.run(consume_stream(resp)) == b"\x89PNGdata"
        assert fake.closed is True
        assert client.closed is True
        # The browser receives only local, path-scoped mirror cookies.
        headers = [
            value.decode("latin-1")
            for name, value in resp.raw_headers
            if name.lower() == b"set-cookie"
        ]
        assert all("Path=/proxy/bkxk.szu.edu.cn/" in value for value in headers)
        assert all("Domain=" not in value for value in headers)

    def test_empty_text_response_is_not_streamed(self, monkeypatch):
        set_logged_session(monkeypatch)
        fake = FakeResponse(status_code=204, headers={"Content-Type": "text/plain"})
        client = monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "empty"))

        assert resp.status_code == 204
        assert resp.body == b""
        assert fake.closed is True
        assert client.closed is True

    def test_set_cookie_is_merged_without_forwarding(self, monkeypatch):
        set_logged_session(monkeypatch, cookie="route=a; JSESSIONID=old; _WEU=oldweu")
        fake = FakeResponse(
            headers={
                "Content-Type": "text/plain",
                "Set-Cookie": ("JSESSIONID=new; Path=/; Expires=Wed, 09 Jun 2027 10:18:14 GMT"),
            },
            text="ok",
        )
        monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(proxy_service.proxy_request(FakeRequest(), "session"))

        assert resp.status_code == 200
        assert "JSESSIONID=new" in config.combined_cookie
        assert "JSESSIONID=old" not in config.combined_cookie
        headers = [
            value.decode("latin-1")
            for name, value in resp.raw_headers
            if name.lower() == b"set-cookie"
        ]
        assert any(value.startswith("JSESSIONID=new;") for value in headers)
        assert all("Path=/proxy/bkxk.szu.edu.cn/" in value for value in headers)

    def test_authserver_cookies_are_saved_for_qr_polling(self, monkeypatch):
        set_logged_session(monkeypatch)
        monkeypatch.setattr(config, "authserver_cookie", "")
        fake = FakeResponse(
            headers={
                "Content-Type": "text/html",
                "Set-Cookie": (
                    "route=auth-route; Path=/authserver; Secure, "
                    "JSESSIONID=auth-session; Path=/authserver; Secure, "
                    "insert_cookie=auth-insert; Path=/; Secure"
                ),
            },
            text="<html>login</html>",
        )
        monkeypatch_proxy_client(monkeypatch, fake)

        resp = asyncio.run(
            proxy_service.proxy_request(
                FakeRequest(),
                "authserver/login",
                proxy_service.backend_service.AUTHSERVER_HOST,
            )
        )

        assert resp.status_code == 200
        assert "route=auth-route" in config.authserver_cookie
        assert "JSESSIONID=auth-session" in config.authserver_cookie
        assert "insert_cookie=auth-insert" in config.authserver_cookie

    def test_each_proxy_request_reads_the_current_shared_session(self, monkeypatch):
        clients = []

        def client_factory(*args, **kwargs):
            client = FakeAsyncClient(
                FakeResponse(headers={"Content-Type": "text/plain"}, text="ok")
            )
            clients.append(client)
            return client

        monkeypatch.setattr(proxy_service.httpx, "AsyncClient", client_factory)
        set_logged_session(monkeypatch, cookie="JSESSIONID=first", token="token-first")
        asyncio.run(proxy_service.proxy_request(FakeRequest(), "first"))

        monkeypatch.setattr(config, "combined_cookie", "JSESSIONID=second")
        monkeypatch.setattr(config, "token", "token-second")
        asyncio.run(proxy_service.proxy_request(FakeRequest(), "second"))

        assert clients[0].sent["headers"]["Cookie"] == "JSESSIONID=first"
        assert clients[0].sent["headers"]["token"] == "token-first"
        assert clients[1].sent["headers"]["Cookie"] == "JSESSIONID=second"
        assert clients[1].sent["headers"]["token"] == "token-second"
