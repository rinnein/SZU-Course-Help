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

import json
import logging
import re
from urllib.parse import urlencode

import httpx
from fastapi import Request
from starlette.responses import Response, StreamingResponse

from school_session import is_session_expired_response
from services import auth_service

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
) -> dict[str, str]:
    """Compose the forwarded request headers, forcing the shared session.

    The browser's own ``Cookie`` header (irrelevant on the local proxy host)
    and any duplicate hop headers are replaced by the server-side shared
    session so only one school session is ever in use.  Only a small allow-list
    of content-oriented client headers is forwarded.
    """
    headers: dict[str, str] = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
        "Host": SCHOOL_HOST,
        "Origin": SCHOOL_ORIGIN,
        "Referer": SCHOOL_REFERER,
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


def rewrite_link_ref(value: str) -> str:
    """Map one URL reference inside school HTML/JS back to the local proxy."""
    text = value
    # Absolute / protocol-relative references to the school host.
    text = re.sub(
        r"(?i)https?://" + re.escape(SCHOOL_HOST) + r"(?=/|$|[\"'\s)])",
        PROXY_PREFIX,
        text,
    )
    text = re.sub(
        r"(?i)//" + re.escape(SCHOOL_HOST) + r"(?=/|$|[\"'\s)])",
        PROXY_PREFIX,
        text,
    )
    return text


def rewrite_proxy_path(value: str) -> str:
    """Rewrite a root-relative school URL into the local proxy namespace."""
    if not value or value.startswith(("//", "http://", "https://")):
        return rewrite_link_ref(value)
    if value.startswith(PROXY_PREFIX) or value.startswith("/api/"):
        return value
    if value.startswith("/"):
        return f"{PROXY_PREFIX}{value}"
    return rewrite_link_ref(value)


_ROOT_RELATIVE_ATTR = re.compile(r'(href|src|action|poster|data|codebase)\s*=\s*["\'](/[^"\']*)["\']')


def rewrite_html(html: str) -> str:
    """Rewrite links inside a school HTML document onto the proxy prefix."""
    rewritten = rewrite_link_ref(html)

    def _replace_root_relative(match: re.Match[str]) -> str:
        attr = match.group(1)
        path = match.group(2)
        if path.startswith("/proxy/") or path.startswith("/api/") or path.startswith("//"):
            return match.group(0)
        return f'{attr}="{PROXY_PREFIX}{path}"'

    return _ROOT_RELATIVE_ATTR.sub(_replace_root_relative, rewritten)


def rewrite_text_body(body: bytes, content_type: str) -> bytes:
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
    rewritten = rewrite_html(text) if head == "text/html" else rewrite_link_ref(text)
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
        return f"{html[:match.end()]}{bootstrap}{html[match.end():]}"
    return bootstrap + html


# ---------------------------------------------------------------------------
# Session merge from Set-Cookie
# ---------------------------------------------------------------------------


def _fold_set_cookie(header_values: list[str]) -> None:
    """Merge school ``Set-Cookie`` values back into the shared session."""
    for header in header_values:
        try:
            if auth_service.merge_session_cookies(header):
                logger.debug("Merged school Set-Cookie into shared session")
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to merge school Set-Cookie: %s", header[:80])


def fold_set_cookie(response: httpx.Response) -> None:
    """Capture response ``Set-Cookie`` before it is stripped from the reply."""
    if not response.headers:
        return
    values = response.headers.get_list("set-cookie")
    if values:
        _fold_set_cookie(values)


def fold_set_cookie_chain(response: httpx.Response) -> None:
    """Merge cookies from redirects and the final upstream response."""
    for previous in response.history:
        fold_set_cookie(previous)
    fold_set_cookie(response)


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


async def proxy_request(request: Request, school_path: str) -> Response:
    """Forward ``/proxy/bkxk.szu.edu.cn/<school_path>`` to the school."""
    logged_in, combined_cookie, token, student_id = auth_service.get_shared_browser_session()
    if not logged_in:
        return _json_error(401, _not_logged_in_message())

    if not school_path:
        school_path = "/"
    if not school_path.startswith("/"):
        school_path = "/" + school_path
    # Map the browser path 1:1 onto the school root host, independent of the
    # API-mode SCHOOL_BASE_URL (which already carries an xsxkapp path segment).
    query_items = []
    for key, value in request.query_params.multi_items():
        query_items.append((key, token if key.lower() == "token" else value))
    query_string = urlencode(query_items)
    upstream_url = SCHOOL_ORIGIN + school_path
    if query_string:
        upstream_url += f"?{query_string}"
    if not upstream_url.startswith(SCHOOL_ORIGIN):
        # Never proxy to an arbitrary origin (SSRF guard).
        return _json_error(400, {"message": "非法的目标路径", "is_error": True})

    client_headers = dict(request.headers.items())
    headers = build_upstream_headers(client_headers, combined_cookie, token)

    body_bytes: bytes | None = None
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.body is not None:
        try:
            body_bytes = await request.body()
        except RuntimeError:
            body_bytes = None

    timeout = httpx.Timeout(30.0, connect=10.0)

    client = httpx.AsyncClient(timeout=timeout, trust_env=True)
    try:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            headers=headers,
            content=body_bytes,
        )
        upstream = await client.send(upstream_request, stream=True, follow_redirects=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("Proxy upstream request failed: %s", type(exc).__name__)
        return _json_error(
            502,
            {"message": "暂时无法连接学校原始页面服务，请稍后重试", "is_error": True},
        )

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
        return _json_error(401, _expiry_message())

    fold_set_cookie_chain(upstream)

    # Rebuild the response headers the client actually should see.
    response_headers = _client_headers_from_upstream(upstream.headers)
    if is_text_response:
        rewritten = rewrite_text_body(upstream_body or b"", content_type)
        if content_head == "text/html":
            rewritten = inject_shared_session_bootstrap(
                rewritten.decode("utf-8", errors="replace"), token, student_id
            ).encode("utf-8")
        await _close_upstream(client, upstream)
        return Response(
            status_code=upstream.status_code,
            content=rewritten,
            headers=response_headers,
            media_type=content_head or "application/octet-stream",
        )

    # Keep the HTTPX client alive until Starlette has consumed the body.
    return StreamingResponse(
        _stream_upstream(client, upstream),
        status_code=upstream.status_code,
        headers=response_headers,
    )


def _client_headers_from_upstream(upstream_headers: httpx.Headers) -> dict[str, str]:
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
            result[name] = rewrite_proxy_path(value)
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
    "fold_set_cookie",
    "fold_set_cookie_chain",
    "inject_shared_session_bootstrap",
    "proxy_request",
    "rewrite_html",
    "rewrite_link_ref",
    "rewrite_proxy_path",
    "rewrite_text_body",
]
