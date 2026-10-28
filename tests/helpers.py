from __future__ import annotations

import io
import json
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit
from wsgiref.util import setup_testing_defaults


def wsgi_request(
    app,
    method: str,
    target: str,
    body: dict[str, Any] | None = None,
    *,
    cookie: str | None = None,
    csrf: str | None = None,
    remote_addr: str = "127.0.0.1",
) -> tuple[int, dict[str, str], dict[str, Any] | str]:
    parsed = urlsplit(target)
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    environ: dict[str, Any] = {}
    setup_testing_defaults(environ)
    environ.update(
        {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": parsed.path,
            "QUERY_STRING": parsed.query,
            "CONTENT_LENGTH": str(len(raw_body)),
            "wsgi.input": io.BytesIO(raw_body),
            "REMOTE_ADDR": remote_addr,
        }
    )
    if body is not None:
        environ["CONTENT_TYPE"] = "application/json"
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    if csrf:
        environ["HTTP_X_CSRF_TOKEN"] = csrf
    captured: dict[str, Any] = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    raw_response = b"".join(app(environ, start_response))
    status_code = int(captured["status"].split()[0])
    headers = {key.lower(): value for key, value in captured["headers"]}
    if "application/json" in headers.get("content-type", "") and raw_response:
        payload: dict[str, Any] | str = json.loads(raw_response.decode("utf-8"))
    else:
        payload = raw_response.decode("utf-8")
    return status_code, headers, payload


def cookie_from_headers(headers: dict[str, str]) -> str:
    cookie = SimpleCookie()
    cookie.load(headers["set-cookie"])
    morsel = cookie["cinema_session"]
    return f"cinema_session={morsel.value}"
