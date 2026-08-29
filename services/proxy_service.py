"""Reverse-proxy to the school's own Web UI, reusing the shared school session.

The local Web UI normally talks to the school server-side through ``/api/*``
endpoints that attach ``config.combined_cookie`` and ``config.token`` on the
server.  This module lets the browser also drive arbitrary ``bkxk.szu.edu.cn``
pages through a same-origin path prefix::

    http://127.0.0.1:<port>/proxy/bkxk.szu.edu.cn/<school-path>

Every proxied request re-reads the *current* shared session from
:func:`services.auth_service.get_shared_session` so an OCR automatic re-login
is honoured immediately and only one school session ever exists.  Because the
browser never performs its own school login through the proxy, switching
between the API workbench and the proxy view never logs the school session
out (the school kicks every previous session on login).

School ``Set-Cookie`` values are folded back into ``config.combined_cookie``
via :func:`services.auth_service.merge_session_cookies` instead of being sent
to the browser, and HTML/JS links are rewritten back to the local proxy prefix
so sub-resources keep carrying the shared session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlencode

import httpx
from fastapi import Request
from starlette.responses import Response, StreamingResponse

import config
from school_session import is_session_expired_response
from services import auth_service, backend_service

logger = logging.getLogger(__name__)

# Local path prefix under which the whole school host is reachable.
SCHOOL_HOST = "bkxk.szu.edu.cn"
PROXY_PREFIX = f"/proxy/{SCHOOL_HOST}"
SCHOOL_ORIGIN = f"http://{SCHOOL_HOST}"
SCHOOL_ENTRY_PATH = "/xsxkapp/sys/xsxkapp/*default/index.do"
SCHOOL_ENTRY_URL = f"{SCHOOL_ORIGIN}{SCHOOL_ENTRY_PATH}"
SCHOOL_REFERER = SCHOOL_ENTRY_URL

# Header template mirroring the API-mode requests (see logic/choose_course).
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 "
    "Safari/537.36 Edg/139.0.0.0"
)


def proxy_prefix(host: str) -> str:
    return f"/proxy/{host}"


def _target_for_route(route_host: str):
    normalized = str(route_host or SCHOOL_HOST).lower()
    if normalized == "auto":
        return None, None
    if normalized in backend_service.PROXY_HOSTS:
        profile = backend_service.get_profile(backend_service.PROXY_HOSTS[normalized])
        return profile, normalized
    if normalized in backend_service.AUTH_PROXY_HOSTS:
        return None, normalized
    return None, None


def _upstream_scheme(host: str, profile=None) -> str:
    if profile is not None:
        return profile.scheme
    return "https"


# Content types whose bytes we are allowed to inspect/rewrite.
_TEXTISH_PREFIXES = (
    "text/html",
    "text/plain",
    "application/javascript",
    "application/x-javascript",
    "text/javascript",
    "application/json",
    "application/xml",
    "text/xml",
)

# Body size cap for HTML/JS/JSON rewriting.  Larger binary payloads stream
# straight through without buffering.
_MAX_REWRITE_BYTES = 8 * 1024 * 1024


class SharedSessionRequiredError(RuntimeError):
    """Raised when the proxy is invoked without an established school session."""


def build_upstream_headers(
    client_headers: dict[str, str],
    combined_cookie: str,
    token: str,
    profile=None,
    upstream_host: str | None = None,
) -> dict[str, str]:
    """Compose the forwarded request headers, forcing the shared session.

    The browser's own ``Cookie`` header (irrelevant on the local proxy host)
    and any duplicate hop headers are replaced by the server-side shared
    session so only one school session is ever in use.  Only a small allow-list
    of content-oriented client headers is forwarded.
    """
    profile = profile or backend_service.get_profile(config.BACKEND_PRIMARY)
    target_host = upstream_host or profile.host
    origin = f"{profile.scheme}://{target_host}"
    headers: dict[str, str] = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
        "Host": target_host,
        "Origin": origin,
        "Referer": f"{origin}{profile.entry_path}",
        "User-Agent": _UA,
        "Cookie": combined_cookie,
        "token": token,
        "X-Requested-With": "XMLHttpRequest",
    }
    # Only a small allow-list of content-oriented headers plus benign custom
    # ``x-*`` headers are forwarded; host/cookie/token/origin/referer/ua are
    # always controlled by the proxy.
    allow_forward = {"content-type", "accept", "content-encoding"}
    for name, value in client_headers.items():
        lowered = name.lower()
        if lowered.startswith("proxy-"):
            continue
        if lowered in allow_forward or lowered.startswith("x-"):
            headers[name] = value
    return headers


# ---------------------------------------------------------------------------
# Link rewriting
# ---------------------------------------------------------------------------


def rewrite_link_ref(value: str, default_host: str = SCHOOL_HOST) -> str:
    """Map one URL reference inside school HTML/JS back to the local proxy."""
    text = value
    for host in (
        backend_service.AUTHSERVER_HOST,
        backend_service.WEBVPN_ROOT_HOST,
        backend_service.WEBVPN_HOST,
        backend_service.PRIMARY_HOST,
    ):
        target_host = (
            backend_service.WEBVPN_HOST
            if host == backend_service.PRIMARY_HOST
            and default_host in {backend_service.WEBVPN_HOST, backend_service.WEBVPN_ROOT_HOST}
            else host
        )
        prefix = proxy_prefix(target_host)
        text = re.sub(
            r"(?i)https?://" + re.escape(host) + r"(?::\d+)?(?=/|$|[\"'\s)])",
            prefix,
            text,
        )
        text = re.sub(
            r"(?i)//" + re.escape(host) + r"(?::\d+)?(?=/|$|[\"'\s)])",
            prefix,
            text,
        )
    return text


def rewrite_proxy_path(value: str, default_host: str = SCHOOL_HOST) -> str:
    """Rewrite a root-relative school URL into the local proxy namespace."""
    if not value or value.startswith(("//", "http://", "https://")):
        return rewrite_link_ref(value, default_host)
    if value.startswith("/proxy/") or value.startswith("/api/"):
        return value
    if value.startswith("/"):
        return f"{proxy_prefix(default_host)}{value}"
    return rewrite_link_ref(value, default_host)


def rewrite_root_relative_refs(value: str, default_host: str) -> str:
    """Rewrite root-relative URL strings inside scripts and JSON responses."""
    prefix = proxy_prefix(default_host)

    def replace(match: re.Match[str]) -> str:
        quote, path = match.group(1), match.group(2)
        if path.startswith(("/proxy/", "/api/", "//")):
            return match.group(0)
        if default_host == backend_service.AUTHSERVER_HOST and not path.startswith("/authserver/"):
            path = f"/authserver{path}"
        return f"{quote}{prefix}{path}{quote}"

    return re.sub(r"([\"'])(/(?!/)[^\"']*)\1", replace, value)


_ROOT_RELATIVE_ATTR = re.compile(
    r'(href|src|action|poster|data|codebase)\s*=\s*["\'](/[^"\']*)["\']'
)


def rewrite_html(html: str, default_host: str = SCHOOL_HOST) -> str:
    """Rewrite links inside a school HTML document onto the proxy prefix."""
    rewritten = rewrite_root_relative_refs(rewrite_link_ref(html, default_host), default_host)

    def _replace_root_relative(match: re.Match[str]) -> str:
        attr = match.group(1)
        path = match.group(2)
        if path.startswith("/proxy/") or path.startswith("/api/") or path.startswith("//"):
            return match.group(0)
        return f'{attr}="{proxy_prefix(default_host)}{path}"'

    return _ROOT_RELATIVE_ATTR.sub(_replace_root_relative, rewritten)


def rewrite_text_body(body: bytes, content_type: str, default_host: str = SCHOOL_HOST) -> bytes:
    """Rewrite links for text bodies; return original bytes for non-text."""
    head = str(content_type or "").split(";")[0].strip().lower()
    if not any(head.startswith(prefix) for prefix in _TEXTISH_PREFIXES):
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    if len(text) > _MAX_REWRITE_BYTES:
        return body
    rewritten = (
        rewrite_html(text, default_host)
        if head == "text/html"
        else rewrite_root_relative_refs(rewrite_link_ref(text, default_host), default_host)
    )
    return rewritten.encode("utf-8")


def inject_shared_session_bootstrap(
    html: str,
    token: str,
    student_id: str,
    proxy_base: str = f"{PROXY_PREFIX}/xsxkapp",
) -> str:
    """Seed the original page's browser state from the shared API session.

    The school UI does not use the token alone.  Its next page synchronously
    reads ``studentInfo``, ``currentBatch``, ``currentCampus``, ``sysParam``
    and ``dictionary`` from ``sessionStorage`` before its own AJAX bootstrap
    has had a chance to run.  A minimal ``studentInfo`` object therefore lets
    the page navigate successfully but makes the course-selection JavaScript
    throw as soon as it reads the missing fields.

    Seed safe defaults first, then synchronously load the same public bootstrap
    endpoints through this proxy.  The requests carry the server-side shared
    session, so this never performs a school login or creates a second school
    session.  Synchronous XHR is intentional here: it completes before the
    original page's external scripts execute.
    """

    def _js_string(value: str) -> str:
        return json.dumps(str(value), ensure_ascii=False).replace("<", "\\u003c")

    token_json = _js_string(token)
    student_json = _js_string(student_id)
    base_json = _js_string(proxy_base.rstrip("/"))
    bootstrap = (
        "<script>"
        "(function(){"
        f"var sharedToken={token_json},studentCode={student_json},base={base_json};"
        "sessionStorage.setItem('token',sharedToken);"
        "sessionStorage.setItem('studentInfo',JSON.stringify({code:studentCode,electiveBatch:{}}));"
        "sessionStorage.setItem('currentBatch',JSON.stringify({}));"
        "sessionStorage.setItem('currentCampus',JSON.stringify({code:'',name:''}));"
        "sessionStorage.setItem('sysParam',JSON.stringify({}));"
        "sessionStorage.setItem('dictionary',JSON.stringify({}));"
        "function load(path){"
        "try{"
        "var xhr=new XMLHttpRequest();"
        "xhr.open('POST',base+path,false);"
        "xhr.setRequestHeader('token',sharedToken);"
        "xhr.setRequestHeader('X-Requested-With','XMLHttpRequest');"
        "xhr.setRequestHeader('Content-Type','application/x-www-form-urlencoded; charset=UTF-8');"
        "xhr.send('');"
        "if(xhr.status>=200&&xhr.status<300)return JSON.parse(xhr.responseText);"
        "}catch(ignore){}"
        "return null;"
        "}"
        "var student=load('/sys/xsxkapp/student/'+encodeURIComponent(studentCode)+'.do');"
        "if(student&&student.code==='1'&&student.data){"
        "var info=student.data;"
        "if(!info.electiveBatch)info.electiveBatch={};"
        "sessionStorage.setItem('studentInfo',JSON.stringify(info));"
        "sessionStorage.setItem('currentBatch',JSON.stringify(info.electiveBatch));"
        "if(info.campus!==null&&info.campus!==undefined&&info.campus!=='')"
        "sessionStorage.setItem('currentCampus',JSON.stringify({code:info.campus,name:info.campusName||''}));"
        "if(info.electiveIsOpen!==null&&info.electiveIsOpen!==undefined)"
        "sessionStorage.setItem('electiveIsOpen',String(info.electiveIsOpen));"
        "}"
        "var sys=load('/sys/xsxkapp/publicinfo/sysparam.do');"
        "if(sys&&sys.code==='1'&&sys.data)sessionStorage.setItem('sysParam',JSON.stringify(sys.data));"
        "var dictionary=load('/sys/xsxkapp/publicinfo/dictionary.do');"
        "if(dictionary&&dictionary.code==='1'&&dictionary.data)"
        "sessionStorage.setItem('dictionary',JSON.stringify(dictionary.data.dictionaryList||{}));"
        "})();"
        "</script>"
    )
    match = re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE)
    if match:
        return f"{html[: match.end()]}{bootstrap}{html[match.end() :]}"
    return bootstrap + html


# ---------------------------------------------------------------------------
# Session merge from Set-Cookie
# ---------------------------------------------------------------------------


def _fold_set_cookie(header_values: list[str], upstream_host: str = SCHOOL_HOST) -> None:
    """Merge school ``Set-Cookie`` values back into the shared session."""
    if upstream_host != SCHOOL_HOST:
        try:
            if auth_service.merge_backend_cookies(header_values, upstream_host):
                logger.debug("Merged %s Set-Cookie into shared session", upstream_host)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to merge %s Set-Cookie", upstream_host)
        return
    for header in header_values:
        try:
            if auth_service.merge_session_cookies(header):
                logger.debug("Merged school Set-Cookie into shared session")
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to merge school Set-Cookie: %s", header[:80])


def _proxy_cookie_path(proxy_host: str) -> str:
    return f"/proxy/{str(proxy_host or SCHOOL_HOST).strip('/')}/"


def proxy_cookie_headers(proxy_host: str) -> list[str]:
    """Build local, path-scoped Cookie headers from the server session.

    The browser receives a compatibility mirror only.  Proxy requests still
    use the server-side session assembled by ``backend_service.cookie_header``.
    """
    host = str(proxy_host or SCHOOL_HOST).lower()
    if host not in backend_service.PROXY_HOSTS:
        return []
    names = list(backend_service.parse_cookie_pairs(getattr(config, "combined_cookie", "")))
    if host in {backend_service.WEBVPN_HOST, backend_service.WEBVPN_ROOT_HOST}:
        names.extend(backend_service.WEBVPN_COOKIE_NAMES)
    combined = backend_service.parse_cookie_pairs(getattr(config, "combined_cookie", ""))
    webvpn = backend_service.parse_cookie_pairs(getattr(config, "webvpn_cookie", ""))
    values = []
    for name in names:
        value = (webvpn if name in backend_service.WEBVPN_COOKIE_NAMES else combined).get(name, "")
        if value:
            values.append(f"{name}={value}")
    path = _proxy_cookie_path(host)
    return [f"{value}; Path={path}; SameSite=Lax" for value in values]


def _append_set_cookie_headers(response: Response, cookie_headers: list[str]) -> Response:
    for header in cookie_headers:
        response.raw_headers.append((b"set-cookie", header.encode("latin-1")))
    return response


def apply_proxy_cookie_mirror(response: Response, proxy_host: str) -> Response:
    """Attach the current server session as multiple local Set-Cookie headers."""
    return _append_set_cookie_headers(response, proxy_cookie_headers(proxy_host))


def clear_proxy_cookie_mirror(response: Response) -> Response:
    """Expire both school proxy Cookie namespaces during logout."""
    for host in (SCHOOL_HOST, backend_service.WEBVPN_HOST, backend_service.WEBVPN_ROOT_HOST):
        path = _proxy_cookie_path(host)
        names = list(backend_service.SCHOOL_COOKIE_NAMES)
        if host != SCHOOL_HOST:
            names.extend(backend_service.WEBVPN_COOKIE_NAMES)
        for name in names:
            response.raw_headers.append(
                (
                    b"set-cookie",
                    f"{name}=; Path={path}; Max-Age=0; SameSite=Lax".encode("latin-1"),
                )
            )
    return response


def fold_set_cookie(response: httpx.Response, upstream_host: str = SCHOOL_HOST) -> None:
    """Capture response ``Set-Cookie`` before it is stripped from the reply."""
    if not response.headers:
        return
    values = response.headers.get_list("set-cookie")
    if values:
        _fold_set_cookie(values, upstream_host)


def fold_set_cookie_chain(response: httpx.Response, upstream_host: str = SCHOOL_HOST) -> None:
    """Merge cookies from redirects and the final upstream response."""
    for previous in response.history:
        fold_set_cookie(previous, upstream_host)
    fold_set_cookie(response, upstream_host)


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _expiry_message() -> dict[str, str]:
    return {
        "message": "登录状态已失效，请回到本地工作台重新登录或等待自动重登后再试",
        "is_error": True,
        "error_code": "PROXY_SESSION_EXPIRED",
        "retryable": True,
    }


def _not_logged_in_message() -> dict[str, str]:
    return {
        "message": "尚未登录。请先在本地面板完成学校登录后再访问学校原始页面。",
        "is_error": True,
        "error_code": "PROXY_NOT_LOGGED_IN",
        "retryable": True,
    }


def _json_error(code: int, payload: dict[str, str]) -> Response:
    return Response(
        status_code=code,
        media_type="application/json",
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Cache-Control": "no-store"},
    )


async def _close_upstream(client: httpx.AsyncClient, upstream: httpx.Response) -> None:
    """Close both the streamed response and its owning HTTPX client."""
    try:
        await upstream.aclose()
    finally:
        await client.aclose()


async def _stream_upstream(
    client: httpx.AsyncClient,
    upstream: httpx.Response,
):
    """Yield an upstream body while keeping HTTPX open until streaming ends."""
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await _close_upstream(client, upstream)


# ---------------------------------------------------------------------------
# Core proxy handler
# ---------------------------------------------------------------------------


async def proxy_request(
    request: Request,
    school_path: str,
    proxy_host: str = SCHOOL_HOST,
    _allow_session_recovery: bool = True,
) -> Response:
    """Forward a school or CAS request through an approved local proxy route."""
    logged_in, _combined_cookie, token, student_id = auth_service.get_shared_browser_session()
    route_host = str(proxy_host or SCHOOL_HOST).lower()
    if not logged_in and route_host == SCHOOL_HOST:
        return _json_error(401, _not_logged_in_message())
    if route_host == "auto":
        candidates = backend_service.candidate_profiles()
    else:
        profile, upstream_host = _target_for_route(route_host)
        if profile is None and upstream_host is None:
            return _json_error(404, {"message": "不支持的代理目标", "is_error": True})
        candidates = [profile] if profile is not None else [None]

    if not school_path:
        school_path = "/"
    if not school_path.startswith("/"):
        school_path = "/" + school_path
    query_items = []
    for key, value in request.query_params.multi_items():
        query_items.append((key, token if key.lower() == "token" and token else value))
    query_string = urlencode(query_items)
    client_headers = dict(request.headers.items())
    body_bytes: bytes | None = None
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.body is not None:
        try:
            body_bytes = await request.body()
        except RuntimeError:
            body_bytes = None

    timeout = httpx.Timeout(30.0, connect=10.0)
    client: httpx.AsyncClient | None = None
    upstream: httpx.Response | None = None
    selected_profile = None
    selected_host = SCHOOL_HOST
    for index, candidate in enumerate(candidates):
        upstream_host = (
            route_host
            if candidate is not None
            and route_host in backend_service.PROXY_HOSTS
            and route_host != "auto"
            else candidate.host
            if candidate is not None
            else route_host
        )
        scheme = _upstream_scheme(upstream_host, candidate)
        if (
            candidate is not None
            and candidate.key == config.BACKEND_WEBVPN
            and not backend_service.has_webvpn_cookies()
        ):
            service = (
                f"https://{backend_service.WEBVPN_ROOT_HOST}"
                "/users/auth/cas/callback?url="
                f"https://{backend_service.WEBVPN_HOST}{school_path}"
            )
            auth_location = (
                f"{proxy_prefix(backend_service.AUTHSERVER_HOST)}"
                "/authserver/login?" + urlencode({"service": service})
            )
            return Response(status_code=302, headers={"Location": auth_location})
        upstream_url = f"{scheme}://{upstream_host}{school_path}"
        if query_string:
            upstream_url += f"?{query_string}"
        if candidate is not None:
            headers = build_upstream_headers(
                client_headers,
                backend_service.cookie_header(candidate),
                token,
                profile=candidate,
                upstream_host=upstream_host,
            )
            if candidate.key == config.BACKEND_WEBVPN:
                headers["Connection"] = "close"
        else:
            headers = {
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cookie": backend_service.cookie_header(
                    backend_service.get_profile(config.BACKEND_WEBVPN),
                    authserver=True,
                ),
                "Host": upstream_host,
                "Origin": f"{scheme}://{upstream_host}",
                "Referer": f"{scheme}://{upstream_host}/authserver/login",
                "User-Agent": _UA,
            }
        client = httpx.AsyncClient(
            timeout=timeout,
            trust_env=True,
            http1=True,
            http2=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=0),
        )
        try:
            # Some WebVPN gateway nodes close an otherwise valid HTTP/1.1
            # connection before HTTPX receives the response headers. Retry once
            # on a fresh connection; business responses are never retried here.
            for attempt in range(2):
                try:
                    upstream_request = client.build_request(
                        request.method,
                        upstream_url,
                        headers=headers,
                        content=body_bytes,
                    )
                    # Browser-visible redirects are required for CAS interaction.
                    # Each next local proxy request carries server-side cookies.
                    follow_redirects = route_host in {
                        SCHOOL_HOST,
                        backend_service.AUTHSERVER_HOST,
                    }
                    upstream = await client.send(
                        upstream_request,
                        stream=True,
                        follow_redirects=follow_redirects,
                    )
                    break
                except (httpx.RemoteProtocolError, httpx.ReadError):
                    if attempt == 0:
                        logger.info(
                            "Proxy upstream %s closed the connection; retrying once",
                            upstream_host,
                        )
                        await client.aclose()
                        client = httpx.AsyncClient(
                            timeout=timeout,
                            trust_env=True,
                            http1=True,
                            http2=False,
                            limits=httpx.Limits(
                                max_connections=10,
                                max_keepalive_connections=0,
                            ),
                        )
                        continue
                    raise
        except httpx.HTTPError as exc:
            await client.aclose()
            client = None
            if candidate is not None:
                backend_service.mark_failure(candidate)
            if index + 1 < len(candidates):
                logger.warning("Proxy backend %s unavailable; trying fallback", upstream_host)
                continue
            logger.warning("Proxy upstream request failed: %s", type(exc).__name__)
            return _json_error(
                502,
                {"message": "暂时无法连接学校原始页面服务，请稍后重试", "is_error": True},
            )
        status_code = upstream.status_code
        if status_code in backend_service.TRANSIENT_STATUS_CODES and candidate is not None:
            backend_service.mark_failure(candidate)
        if status_code in backend_service.TRANSIENT_STATUS_CODES and index + 1 < len(candidates):
            await _close_upstream(client, upstream)
            client = None
            upstream = None
            logger.warning(
                "Proxy backend %s returned %s; trying fallback", upstream_host, status_code
            )
            continue
        selected_profile = candidate
        selected_host = upstream_host
        if candidate is not None:
            backend_service.mark_success(candidate)
        break

    if client is None or upstream is None:
        return _json_error(502, {"message": "没有可用的学校后端", "is_error": True})

    content_type = upstream.headers.get("content-type", "")
    content_head = content_type.split(";", 1)[0].strip().lower()
    is_text_response = any(content_head.startswith(prefix) for prefix in _TEXTISH_PREFIXES)
    upstream_body: bytes | None = None
    upstream_text = ""

    if is_text_response:
        try:
            upstream_body = await upstream.aread()
        except httpx.HTTPError as exc:
            await _close_upstream(client, upstream)
            logger.warning("Proxy upstream response read failed: %s", type(exc).__name__)
            return _json_error(
                502,
                {"message": "学校原始页面响应读取失败，请稍后重试", "is_error": True},
            )
        upstream_text = upstream_body.decode("utf-8", errors="replace")

    # Capture cookies before handling redirects or expiry responses. The CAS
    # login page sets route/JSESSIONID/insert_cookie on its first response,
    # and the QR polling requests need those cookies immediately.
    fold_set_cookie_chain(upstream, selected_host)

    # Detect expired sessions so we never reflect the school login page.
    status_for_expiry = upstream.status_code
    if upstream.status_code == 302 and upstream.headers.get("location"):
        # A redirect with a target is a normal school navigation. HTTPX has
        # already followed it above; only a bare 302 is treated as rejection.
        status_for_expiry = None
    expired = is_session_expired_response(
        status_code=status_for_expiry,
        text=upstream_text,
    )
    if expired:
        logger.info("Proxy request detected an expired shared session")
        await _close_upstream(client, upstream)
        if _allow_session_recovery and auth_service.automatic_relogin_available():
            logger.info("Proxy request starting OCR session recovery")
            recovered, error = await asyncio.to_thread(
                auth_service.attempt_automatic_relogin,
                config.ocr_relogin_max_attempts,
            )
            if recovered:
                # Rebuild the upstream request with the newly issued shared
                # cookies/token. Retry exactly once to avoid a recovery loop.
                return await proxy_request(
                    request,
                    school_path,
                    proxy_host,
                    _allow_session_recovery=False,
                )
            logger.warning("Proxy OCR session recovery failed: %s", error)
        return _json_error(401, _expiry_message())

    if upstream.status_code in {301, 302, 303, 307, 308} and upstream.headers.get("location"):
        # Keep browser navigation inside the local proxy. The next request
        # will carry the server-side CAS/WebVPN cookie state.
        response_headers = _client_headers_from_upstream(upstream.headers, selected_host)
        await _close_upstream(client, upstream)
        return apply_proxy_cookie_mirror(
            Response(status_code=upstream.status_code, headers=response_headers),
            selected_host,
        )

    # Rebuild the response headers the client actually should see.
    response_headers = _client_headers_from_upstream(upstream.headers, selected_host)
    if is_text_response:
        response_host = selected_host or route_host
        rewritten = rewrite_text_body(upstream_body or b"", content_type, response_host)
        if content_head == "text/html" and selected_profile is not None and logged_in:
            rewritten = inject_shared_session_bootstrap(
                rewritten.decode("utf-8", errors="replace"),
                token,
                student_id,
                proxy_base=f"{proxy_prefix(response_host)}/xsxkapp",
            ).encode("utf-8")
        await _close_upstream(client, upstream)
        return apply_proxy_cookie_mirror(
            Response(
                status_code=upstream.status_code,
                content=rewritten,
                headers=response_headers,
                media_type=content_head or "application/octet-stream",
            ),
            selected_host,
        )

    # Keep the HTTPX client alive until Starlette has consumed the body.
    return apply_proxy_cookie_mirror(
        StreamingResponse(
            _stream_upstream(client, upstream),
            status_code=upstream.status_code,
            headers=response_headers,
        ),
        selected_host,
    )


def _client_headers_from_upstream(
    upstream_headers: httpx.Headers,
    default_host: str = SCHOOL_HOST,
) -> dict[str, str]:
    """Pick response headers safe to send back, rewriting Location and links."""
    result: dict[str, str] = {}
    for name, value in upstream_headers.items():
        lowered = name.lower()
        # Never forward Set-Cookie (session is merged server-side).
        if lowered == "set-cookie":
            continue
        # Hop-by-hop / body-invalidating headers the proxy manages itself.
        if lowered in (
            "transfer-encoding",
            "connection",
            "keep-alive",
            "content-encoding",
            "content-length",
        ):
            continue
        if lowered == "location":
            result[name] = rewrite_proxy_path(value, default_host)
        else:
            result[name] = value
    return result


__all__ = [
    "PROXY_PREFIX",
    "SCHOOL_HOST",
    "SCHOOL_ENTRY_PATH",
    "SCHOOL_ENTRY_URL",
    "SCHOOL_ORIGIN",
    "SharedSessionRequiredError",
    "build_upstream_headers",
    "apply_proxy_cookie_mirror",
    "clear_proxy_cookie_mirror",
    "fold_set_cookie",
    "fold_set_cookie_chain",
    "inject_shared_session_bootstrap",
    "proxy_request",
    "proxy_cookie_headers",
    "rewrite_html",
    "rewrite_link_ref",
    "rewrite_proxy_path",
    "rewrite_text_body",
]
