from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Pattern
from urllib.parse import parse_qs

from .config import Config
from .db import Database
from .errors import APIError
from .service import CinemaService


LOGGER = logging.getLogger("cinema")
SESSION_COOKIE = "cinema_session"
MAX_JSON_BYTES = 1_000_000


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    cookies: dict[str, str]
    body: bytes
    remote_addr: str

    def json(self) -> dict[str, Any]:
        content_type = self.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise APIError(415, "unsupported_media_type", "Требуется Content-Type application/json")
        try:
            value = json.loads(self.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(400, "invalid_json", "Тело запроса содержит некорректный JSON") from exc
        if not isinstance(value, dict):
            raise APIError(422, "validation_error", "Корнем JSON должен быть объект")
        return value


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Route:
    method: str
    pattern: Pattern[str]
    handler: Callable[..., Response]


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        threshold = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= threshold:
                events.popleft()
            if len(events) >= self.limit:
                raise APIError(
                    429,
                    "rate_limit_exceeded",
                    "Слишком много попыток. Повторите запрос через минуту",
                )
            events.append(now)


class CinemaApplication:
    def __init__(self, config: Config, service: CinemaService) -> None:
        self.config = config
        self.service = service
        self.static_dir = config.root_dir / "static"
        self.auth_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
        self.routes = self._build_routes()

    def _build_routes(self) -> list[Route]:
        return [
            Route("GET", re.compile(r"^/api/health$"), self.health),
            Route("GET", re.compile(r"^/api/me$"), self.me),
            Route("POST", re.compile(r"^/api/auth/register$"), self.register),
            Route("POST", re.compile(r"^/api/auth/login$"), self.login),
            Route("POST", re.compile(r"^/api/auth/logout$"), self.logout),
            Route("GET", re.compile(r"^/api/movies$"), self.movies),
            Route("GET", re.compile(r"^/api/movies/(?P<movie_id>\d+)$"), self.movie),
            Route("GET", re.compile(r"^/api/screenings$"), self.screenings),
            Route(
                "GET",
                re.compile(r"^/api/screenings/(?P<screening_id>\d+)/seats$"),
                self.screening_seats,
            ),
            Route("GET", re.compile(r"^/api/bookings$"), self.bookings),
            Route("POST", re.compile(r"^/api/bookings$"), self.create_booking),
            Route("GET", re.compile(r"^/api/bookings/(?P<booking_id>\d+)$"), self.booking),
            Route("DELETE", re.compile(r"^/api/bookings/(?P<booking_id>\d+)$"), self.delete_booking),
            Route(
                "POST",
                re.compile(r"^/api/bookings/(?P<booking_id>\d+)/cancel$"),
                self.cancel_booking,
            ),
            Route("GET", re.compile(r"^/api/admin/halls$"), self.admin_halls),
            Route("POST", re.compile(r"^/api/admin/halls$"), self.admin_create_hall),
            Route(
                "PATCH",
                re.compile(r"^/api/admin/halls/(?P<hall_id>\d+)$"),
                self.admin_update_hall,
            ),
            Route("POST", re.compile(r"^/api/admin/movies$"), self.admin_create_movie),
            Route("PUT", re.compile(r"^/api/admin/movies/(?P<movie_id>\d+)$"), self.admin_replace_movie),
            Route(
                "PATCH",
                re.compile(r"^/api/admin/movies/(?P<movie_id>\d+)$"),
                self.admin_update_movie,
            ),
            Route(
                "POST",
                re.compile(r"^/api/admin/screenings$"),
                self.admin_create_screening,
            ),
            Route(
                "PATCH",
                re.compile(r"^/api/admin/screenings/(?P<screening_id>\d+)/price$"),
                self.admin_update_screening_price,
            ),
        ]

    def __call__(self, environ: dict[str, Any], start_response: Callable) -> list[bytes]:
        request_id = uuid.uuid4().hex[:12]
        try:
            request = self._make_request(environ)
            response = self._dispatch(request)
        except APIError as exc:
            payload: dict[str, Any] = {
                "error": {"code": exc.code, "message": exc.message},
                "request_id": request_id,
            }
            if exc.details is not None:
                payload["error"]["details"] = exc.details
            response = self._json_response(payload, status=exc.status)
            if exc.status == 405:
                response.headers.append(("Allow", self._allowed_methods(request.path)))
        except Exception:
            LOGGER.exception("Unhandled request error request_id=%s", request_id)
            response = self._json_response(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Внутренняя ошибка сервера",
                    },
                    "request_id": request_id,
                },
                status=500,
            )

        if not any(key.lower() == "cache-control" for key, _ in response.headers):
            response.headers.append(("Cache-Control", "no-store"))
        response.headers.extend(
            [
                ("X-Request-ID", request_id),
                ("X-Content-Type-Options", "nosniff"),
                ("X-Frame-Options", "DENY"),
                ("Referrer-Policy", "same-origin"),
                ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
                (
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'",
                ),
            ]
        )
        accept_encoding = environ.get("HTTP_ACCEPT_ENCODING", "")
        content_type = next(
            (value for key, value in response.headers if key.lower() == "content-type"), ""
        )
        if (
            "gzip" in accept_encoding.lower()
            and len(response.body) > 1024
            and (content_type.startswith("text/") or "json" in content_type or "javascript" in content_type)
            and response.status not in {204, 304}
        ):
            response.body = gzip.compress(response.body, compresslevel=6)
            response.headers.append(("Content-Encoding", "gzip"))
            response.headers.append(("Vary", "Accept-Encoding"))
        response.headers = [
            (key, value) for key, value in response.headers if key.lower() != "content-length"
        ]
        response.headers.append(("Content-Length", str(len(response.body))))
        status = HTTPStatus(response.status)
        start_response(f"{status.value} {status.phrase}", response.headers)
        return [response.body]

    def _make_request(self, environ: dict[str, Any]) -> Request:
        raw_length = environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise APIError(400, "invalid_content_length", "Некорректная длина запроса") from exc
        if length < 0 or length > MAX_JSON_BYTES:
            raise APIError(413, "payload_too_large", "Тело запроса превышает 1 МБ")
        body = environ.get("wsgi.input", io.BytesIO()).read(length) if length else b""
        headers: dict[str, str] = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").lower()] = str(value)
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = str(environ["CONTENT_TYPE"])
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        return Request(
            method=str(environ.get("REQUEST_METHOD", "GET")).upper(),
            path=str(environ.get("PATH_INFO", "/")),
            query=parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True),
            headers=headers,
            cookies={key: morsel.value for key, morsel in cookie.items()},
            body=body,
            remote_addr=str(environ.get("REMOTE_ADDR", "unknown")),
        )

    def _dispatch(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            allowed = self._allowed_methods(request.path)
            if not allowed:
                raise APIError(404, "not_found", "Ресурс не найден")
            return Response(204, headers=[("Allow", allowed)])
        if request.method == "GET" and request.path in {
            "/",
            "/index.html",
            "/styles.css",
            "/app.js",
            "/favicon.svg",
            "/openapi.yaml",
        }:
            return self._static(request)
        path_exists = False
        for route in self.routes:
            match = route.pattern.fullmatch(request.path)
            if match is None:
                continue
            path_exists = True
            if request.method == route.method:
                return route.handler(request, **match.groupdict())
        if path_exists:
            raise APIError(405, "method_not_allowed", "Метод не поддерживается для этого адреса")
        raise APIError(404, "not_found", "Ресурс не найден")

    def _allowed_methods(self, path: str) -> str:
        methods = {route.method for route in self.routes if route.pattern.fullmatch(path)}
        return ", ".join(sorted(methods | {"OPTIONS"})) if methods else ""

    def _static(self, request: Request) -> Response:
        mapping = {
            "/": self.static_dir / "index.html",
            "/index.html": self.static_dir / "index.html",
            "/styles.css": self.static_dir / "styles.css",
            "/app.js": self.static_dir / "app.js",
            "/favicon.svg": self.static_dir / "favicon.svg",
            "/openapi.yaml": self.config.root_dir / "docs" / "openapi.yaml",
        }
        path = mapping[request.path]
        if not path.is_file():
            raise APIError(404, "not_found", "Файл не найден")
        body = path.read_bytes()
        etag = f'"{hashlib.sha256(body).hexdigest()[:20]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(304, headers=[("ETag", etag), ("Cache-Control", "public, max-age=60")])
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in {".html", ".yaml"}:
            media_type += "; charset=utf-8"
        return Response(
            200,
            body,
            [
                ("Content-Type", media_type),
                ("Cache-Control", "no-cache" if path.suffix == ".html" else "public, max-age=3600"),
                ("ETag", etag),
            ],
        )

    def _json_response(
        self, payload: Any, *, status: int = 200, headers: list[tuple[str, str]] | None = None
    ) -> Response:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return Response(
            status,
            body,
            [("Content-Type", "application/json; charset=utf-8"), *(headers or [])],
        )

    def _public_json(self, request: Request, payload: Any) -> Response:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = f'"{hashlib.sha256(body).hexdigest()[:20]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(304, headers=[("ETag", etag), ("Cache-Control", "public, max-age=30")])
        return Response(
            200,
            body,
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", "public, max-age=30"),
                ("ETag", etag),
            ],
        )

    def _auth(self, request: Request) -> dict[str, Any] | None:
        return self.service.authenticate(request.cookies.get(SESSION_COOKIE))

    def _require_user(self, request: Request) -> dict[str, Any]:
        auth = self._auth(request)
        if auth is None:
            raise APIError(401, "authentication_required", "Войдите в систему")
        return auth

    def _require_admin(self, request: Request) -> dict[str, Any]:
        auth = self._require_user(request)
        if auth["role"] != "admin":
            raise APIError(403, "admin_required", "Требуются права администратора")
        return auth

    def _require_csrf(self, request: Request, auth: dict[str, Any]) -> None:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, auth["csrf_token"]):
            raise APIError(403, "csrf_failed", "Проверка безопасности запроса не пройдена")

    def _session_cookie(self, token: str) -> str:
        parts = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={self.config.session_hours * 3600}",
        ]
        if self.config.cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    def _clear_session_cookie(self) -> str:
        parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if self.config.cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    # Route handlers
    def health(self, request: Request) -> Response:
        healthy = self.service.database.is_healthy()
        return self._json_response(
            {"status": "ok" if healthy else "unhealthy"},
            status=200 if healthy else 503,
            headers=[("Cache-Control", "no-store")],
        )

    def me(self, request: Request) -> Response:
        auth = self._auth(request)
        if auth is None:
            return self._json_response(
                {"user": None, "csrf_token": None}, headers=[("Cache-Control", "no-store")]
            )
        return self._json_response(
            {
                "user": {"id": auth["id"], "email": auth["email"], "role": auth["role"]},
                "csrf_token": auth["csrf_token"],
            },
            headers=[("Cache-Control", "no-store")],
        )

    def register(self, request: Request) -> Response:
        self.auth_limiter.check(request.remote_addr)
        user, token, csrf = self.service.register(request.json())
        return self._json_response(
            {"user": user, "csrf_token": csrf},
            status=201,
            headers=[("Set-Cookie", self._session_cookie(token)), ("Cache-Control", "no-store")],
        )

    def login(self, request: Request) -> Response:
        self.auth_limiter.check(request.remote_addr)
        user, token, csrf = self.service.login(request.json())
        return self._json_response(
            {"user": user, "csrf_token": csrf},
            headers=[("Set-Cookie", self._session_cookie(token)), ("Cache-Control", "no-store")],
        )

    def logout(self, request: Request) -> Response:
        auth = self._require_user(request)
        self._require_csrf(request, auth)
        self.service.logout(request.cookies.get(SESSION_COOKIE))
        return self._json_response(
            {"ok": True},
            headers=[("Set-Cookie", self._clear_session_cookie()), ("Cache-Control", "no-store")],
        )

    def movies(self, request: Request) -> Response:
        return self._public_json(request, {"movies": self.service.list_movies()})

    def movie(self, request: Request, movie_id: str) -> Response:
        movie = next((item for item in self.service.list_movies() if item["id"] == int(movie_id)), None)
        if movie is None:
            raise APIError(404, "movie_not_found", "Фильм не найден")
        return self._public_json(request, {"movie": movie})

    def screenings(self, request: Request) -> Response:
        movie_id: int | None = None
        if request.query.get("movie_id", [""])[0]:
            try:
                movie_id = int(request.query["movie_id"][0])
            except ValueError as exc:
                raise APIError(422, "validation_error", "movie_id должен быть целым числом") from exc
        date_value = request.query.get("date", [None])[0]
        return self._public_json(
            request,
            {"screenings": self.service.list_screenings(movie_id, date_value)},
        )

    def screening_seats(self, request: Request, screening_id: str) -> Response:
        return self._json_response(
            self.service.screening_seats(int(screening_id)),
            headers=[("Cache-Control", "no-store")],
        )

    def bookings(self, request: Request) -> Response:
        auth = self._require_user(request)
        return self._json_response(
            {"bookings": self.service.list_bookings(int(auth["id"]))},
            headers=[("Cache-Control", "no-store")],
        )

    def create_booking(self, request: Request) -> Response:
        auth = self._require_user(request)
        self._require_csrf(request, auth)
        booking = self.service.create_booking(int(auth["id"]), request.json())
        return self._json_response(
            {"booking": booking}, status=201,
            headers=[("Cache-Control", "no-store"), ("Location", f"/api/bookings/{booking['id']}")]
        )

    def booking(self, request: Request, booking_id: str) -> Response:
        auth = self._require_user(request)
        return self._json_response({"booking": self.service.get_booking(int(auth["id"]), int(booking_id))})

    def delete_booking(self, request: Request, booking_id: str) -> Response:
        auth = self._require_user(request)
        self._require_csrf(request, auth)
        self.service.cancel_booking(int(auth["id"]), int(booking_id), idempotent=True)
        return Response(204)

    def cancel_booking(self, request: Request, booking_id: str) -> Response:
        auth = self._require_user(request)
        self._require_csrf(request, auth)
        booking = self.service.cancel_booking(int(auth["id"]), int(booking_id))
        return self._json_response(
            {"booking": booking}, headers=[("Cache-Control", "no-store")]
        )

    def admin_halls(self, request: Request) -> Response:
        self._require_admin(request)
        return self._json_response(
            {"halls": self.service.list_halls()}, headers=[("Cache-Control", "no-store")]
        )

    def admin_create_hall(self, request: Request) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        hall = self.service.create_hall(int(auth["id"]), request.json())
        return self._json_response({"hall": hall}, status=201)

    def admin_update_hall(self, request: Request, hall_id: str) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        hall = self.service.update_hall(int(auth["id"]), int(hall_id), request.json())
        return self._json_response({"hall": hall})

    def admin_create_movie(self, request: Request) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        movie = self.service.create_movie(int(auth["id"]), request.json())
        return self._json_response({"movie": movie}, status=201, headers=[("Location", f"/api/movies/{movie['id']}")])

    def admin_replace_movie(self, request: Request, movie_id: str) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        payload = request.json()
        fields = {"title", "description", "duration_min", "age_rating", "active"}
        if set(payload) != fields:
            raise APIError(422, "validation_error", "PUT требует title, description, duration_min, age_rating и active")
        movie = self.service.update_movie(int(auth["id"]), int(movie_id), payload)
        return self._json_response({"movie": movie})

    def admin_update_movie(self, request: Request, movie_id: str) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        movie = self.service.update_movie(int(auth["id"]), int(movie_id), request.json())
        return self._json_response({"movie": movie})

    def admin_create_screening(self, request: Request) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        screening = self.service.create_screening(int(auth["id"]), request.json())
        return self._json_response({"screening": screening}, status=201)

    def admin_update_screening_price(self, request: Request, screening_id: str) -> Response:
        auth = self._require_admin(request)
        self._require_csrf(request, auth)
        screening = self.service.update_screening_price(
            int(auth["id"]), int(screening_id), request.json()
        )
        return self._json_response({"screening": screening})


def create_application(config: Config | None = None, *, seed: bool = True) -> CinemaApplication:
    actual_config = config or Config.from_env()
    database = Database(actual_config)
    database.initialize(seed=seed)
    return CinemaApplication(actual_config, CinemaService(database, actual_config))
