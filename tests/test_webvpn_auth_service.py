from services import webvpn_auth_service


def test_build_auth_url_uses_real_authserver_and_webvpn_callback():
    url = webvpn_auth_service.build_auth_url()

    assert url.startswith("https://authserver-443.webvpn.szu.edu.cn/authserver/login?")
    assert "webvpn.szu.edu.cn%2Fusers%2Fauth%2Fcas%2Fcallback" in url
    assert "bkxk.webvpn.szu.edu.cn" in url


def test_extract_webvpn_cookie_header_filters_names_and_domains():
    cookies = [
        {"name": "_webvpn_key", "value": "key", "domain": ".webvpn.szu.edu.cn"},
        {"name": "webvpn_username", "value": "user", "domain": "bkxk.webvpn.szu.edu.cn"},
        {
            "name": "webvpn_username_NS_Sig",
            "value": "signature",
            "domain": "webvpn.szu.edu.cn",
        },
        {"name": "JSESSIONID", "value": "wrong-host", "domain": "evil.example"},
        {"name": "_webvpn_key", "value": "wrong-host", "domain": "evil.example"},
    ]

    assert webvpn_auth_service.extract_webvpn_cookie_header(cookies) == (
        "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=signature"
    )


def test_extract_webvpn_cookie_header_requires_all_three_cookies():
    assert (
        webvpn_auth_service.extract_webvpn_cookie_header(
            [{"name": "_webvpn_key", "value": "key", "domain": "webvpn.szu.edu.cn"}]
        )
        == ""
    )


def test_controlled_browser_profile_is_persistent(monkeypatch, tmp_path):
    manager = webvpn_auth_service.ControlledBrowserManager()
    profile = tmp_path / "webvpn-browser-profile"
    monkeypatch.setattr(webvpn_auth_service, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(webvpn_auth_service, "find_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(webvpn_auth_service, "_free_local_port", lambda: 43123)
    monkeypatch.setattr(webvpn_auth_service, "_read_http_json", lambda *args, **kwargs: {})

    class FakeProcess:
        def __init__(self):
            self.dead = False

        def poll(self):
            return 0 if self.dead else None

        def terminate(self):
            self.dead = True

        def wait(self, timeout):
            return None

    monkeypatch.setattr(
        webvpn_auth_service.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(webvpn_auth_service, "_open_devtools_page", lambda *args, **kwargs: {})
    monkeypatch.setattr(manager, "_inject_cookies_and_navigate", lambda url: None)
    monkeypatch.setattr(webvpn_auth_service.backend_service, "has_webvpn_cookies", lambda: False)

    status = manager.start()
    assert status["state"] in {"starting", "pending"}
    assert profile.is_dir()
    manager.stop_browser()
    assert profile.is_dir()


def test_saved_cookies_are_injected_before_webvpn_navigation(monkeypatch):
    monkeypatch.setattr(
        webvpn_auth_service.config,
        "combined_cookie",
        "route=route; insert_cookie=insert; JSESSIONID=session; _WEU=weu",
    )
    monkeypatch.setattr(
        webvpn_auth_service.config,
        "webvpn_cookie",
        "_webvpn_key=key; webvpn_username=user; webvpn_username_NS_Sig=sig",
    )
    commands = []
    injected = []
    manager = webvpn_auth_service.ControlledBrowserManager()
    monkeypatch.setattr(
        webvpn_auth_service,
        "_read_http_json",
        lambda *args, **kwargs: [
            {"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools"}
        ],
    )

    def fake_cdp(ws, method, params=None):
        commands.append((method, params))
        if method == "Network.setCookies":
            injected.extend(params["cookies"])
        if method == "Network.getAllCookies":
            return {
                "cookies": [
                    {"name": cookie["name"], "value": cookie["value"], "domain": cookie["domain"]}
                    for cookie in injected
                ]
            }
        return {}

    monkeypatch.setattr(webvpn_auth_service, "_cdp_command", fake_cdp)
    manager._debug_port = 12345
    manager._inject_cookies_and_navigate("https://bkxk.webvpn.szu.edu.cn")

    assert commands[0][0] == "Network.setCookies"
    assert all("domain" in cookie for cookie in commands[0][1]["cookies"])
    domains = {cookie["name"]: cookie["domain"] for cookie in commands[0][1]["cookies"]}
    assert domains["route"] == "bkxk.webvpn.szu.edu.cn"
    assert domains["JSESSIONID"] == "bkxk.webvpn.szu.edu.cn"
    assert domains["_webvpn_key"] == ".webvpn.szu.edu.cn"
    cookies = {cookie["name"]: cookie for cookie in commands[0][1]["cookies"]}
    assert cookies["_WEU"]["path"] == "/xsxkapp/"
    assert cookies["_WEU"]["sameSite"] == "None"
    assert cookies["route"]["path"] == "/"
    assert cookies["route"]["sameSite"] == "Lax"
    assert {cookie["name"] for cookie in commands[0][1]["cookies"]} == {
        "route",
        "insert_cookie",
        "JSESSIONID",
        "_WEU",
        "_webvpn_key",
        "webvpn_username",
        "webvpn_username_NS_Sig",
    }
    assert commands[1][0] == "Network.getAllCookies"
    assert commands[2] == ("Page.navigate", {"url": "https://bkxk.webvpn.szu.edu.cn"})


def test_saved_browser_cookies_include_unknown_school_cookie(monkeypatch):
    monkeypatch.setattr(webvpn_auth_service.config, "combined_cookie", "extra_school=1")
    monkeypatch.setattr(webvpn_auth_service.config, "webvpn_cookie", "")

    cookies = webvpn_auth_service.ControlledBrowserManager._saved_browser_cookies()

    assert cookies == [
        {
            "name": "extra_school",
            "value": "1",
            "domain": "bkxk.webvpn.szu.edu.cn",
            "path": "/",
            "sameSite": "Lax",
            "secure": True,
        }
    ]
