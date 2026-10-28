from __future__ import annotations

import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

from tests import test_api
from tests.helpers import cookie_from_headers, wsgi_request


class RESTTests(unittest.TestCase):
    setUp = test_api.APITests.setUp
    tearDown = test_api.APITests.tearDown
    register = test_api.APITests.register

    def login_admin(self):
        status, headers, payload = wsgi_request(
            self.app, "POST", "/api/auth/login",
            {"email": "admin@cinema.local", "password": "AdminPass1"},
        )
        self.assertEqual(status, 200)
        return cookie_from_headers(headers), payload["csrf_token"]

    def reserve(self, cookie, csrf, mode="reserve", seat_ids=None):
        _, _, data = wsgi_request(self.app, "GET", "/api/screenings")
        screening_id = data["screenings"][0]["id"]
        _, _, data = wsgi_request(self.app, "GET", f"/api/screenings/{screening_id}/seats")
        return wsgi_request(
            self.app, "POST", "/api/bookings",
            {"screening_id": screening_id, "seat_ids": seat_ids or [data["seats"][0]["id"]], "mode": mode},
            cookie=cookie, csrf=csrf,
        )

    def test_booking_location_detail_delete_and_repeat(self):
        cookie, csrf, _ = self.register()
        status, headers, payload = self.reserve(cookie, csrf)
        self.assertEqual(status, 201)
        path = headers["location"]
        status, headers, detail = wsgi_request(self.app, "GET", path, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(detail, payload)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("user_id", detail["booking"])
        for _ in range(2):
            status, headers, body = wsgi_request(self.app, "DELETE", path, cookie=cookie, csrf=csrf)
            self.assertEqual((status, body), (204, ""))
        _, _, data = wsgi_request(self.app, "GET", path, cookie=cookie)
        self.assertEqual(data["booking"]["status"], "cancelled")
        self.assertEqual(self.reserve(cookie, csrf)[0], 201)

    def test_booking_privacy_and_delete_csrf(self):
        owner, token, _ = self.register()
        _, headers, _ = self.reserve(owner, token)
        path = headers["location"]
        stranger, csrf, _ = self.register("stranger@example.org", "127.0.0.20")
        self.assertEqual(wsgi_request(self.app, "GET", path)[0], 401)
        self.assertEqual(wsgi_request(self.app, "GET", path, cookie=stranger)[0], 404)
        self.assertEqual(wsgi_request(self.app, "DELETE", path, cookie=stranger, csrf=csrf)[0], 404)
        self.assertEqual(wsgi_request(self.app, "DELETE", path, cookie=owner)[0], 403)

    def test_paid_booking_cannot_be_deleted(self):
        cookie, csrf, _ = self.register()
        _, headers, _ = self.reserve(cookie, csrf, "purchase")
        self.assertEqual(wsgi_request(self.app, "DELETE", headers["location"], cookie=cookie, csrf=csrf)[0], 409)

    def test_movie_post_put_get_and_patch(self):
        cookie, csrf = self.login_admin()
        movie = {"title": "Тестовый фильм", "description": "Описание", "duration_min": 90, "age_rating": "12+"}
        status, headers, payload = wsgi_request(self.app, "POST", "/api/admin/movies", movie, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 201)
        public_path = headers["location"]
        path = f"/api/admin/movies/{payload['movie']['id']}"
        replacement = {**movie, "title": "Новое название", "active": True}
        self.assertEqual(wsgi_request(self.app, "PUT", path, replacement, cookie=cookie)[0], 403)
        self.assertEqual(wsgi_request(self.app, "PUT", path, movie, cookie=cookie, csrf=csrf)[0], 422)
        for _ in range(2):
            status, _, payload = wsgi_request(self.app, "PUT", path, replacement, cookie=cookie, csrf=csrf)
            self.assertEqual(status, 200)
            self.assertEqual(payload["movie"]["title"], replacement["title"])
        status, _, payload = wsgi_request(self.app, "GET", public_path)
        self.assertEqual(status, 200)
        self.assertEqual(payload["movie"]["title"], replacement["title"])
        self.assertEqual(wsgi_request(self.app, "PATCH", path, {"active": False}, cookie=cookie, csrf=csrf)[0], 200)
        self.assertEqual(wsgi_request(self.app, "GET", public_path)[0], 404)

    def test_put_requires_admin(self):
        cookie, csrf, _ = self.register()
        self.assertEqual(wsgi_request(self.app, "PUT", "/api/admin/movies/1", {}, cookie=cookie, csrf=csrf)[0], 403)
        self.assertEqual(wsgi_request(self.app, "PUT", "/api/admin/movies/1", {})[0], 401)

    def test_allow_and_unknown_resource(self):
        status, headers, _ = wsgi_request(self.app, "OPTIONS", "/api/bookings/1")
        self.assertEqual(status, 204)
        self.assertEqual(set(headers["allow"].split(", ")), {"GET", "DELETE", "OPTIONS"})
        status, headers, _ = wsgi_request(self.app, "PUT", "/api/bookings/1", {})
        self.assertEqual(status, 405)
        self.assertIn("DELETE", headers["allow"])
        self.assertEqual(wsgi_request(self.app, "OPTIONS", "/api/missing")[0], 404)

    def test_invalid_json_and_media_type(self):
        for raw, content_type, expected in [
            (b"{", "application/json", 400),
            (b"[]", "application/json", 422),
            (b"{}", "text/plain", 415),
            (b"{}", "application/json-invalid", 415),
        ]:
            environ = {}
            setup_testing_defaults(environ)
            environ.update(REQUEST_METHOD="POST", PATH_INFO="/api/auth/register", CONTENT_TYPE=content_type,
                           CONTENT_LENGTH=str(len(raw)), **{"wsgi.input": io.BytesIO(raw)})
            captured = {}
            body = b"".join(self.app(environ, lambda status, headers: captured.update(status=int(status[:3]))))
            self.assertEqual(captured["status"], expected)
            self.assertIn("request_id", json.loads(body))

    def test_fractional_and_boolean_ids_and_bad_mode(self):
        cookie, csrf, _ = self.register()
        for value in (1.5, True, {}, [], None):
            self.assertEqual(self.reserve(cookie, csrf, seat_ids=[value])[0], 422)
        self.assertEqual(self.reserve(cookie, csrf, mode=[])[0], 422)
        admin, token = self.login_admin()
        self.assertEqual(wsgi_request(self.app, "PATCH", "/api/admin/screenings/1/price",
                                     {"base_price_cents": 1.5}, cookie=admin, csrf=token)[0], 422)

    def test_inactive_movie_or_hall_cannot_be_booked(self):
        cookie, csrf, _ = self.register()
        _, _, data = wsgi_request(self.app, "GET", "/api/screenings")
        screening = data["screenings"][0]
        admin, token = self.login_admin()
        for kind, ident in (("movies", screening["movie_id"]), ("halls", screening["hall_id"])):
            path = f"/api/admin/{kind}/{ident}"
            self.assertEqual(wsgi_request(self.app, "PATCH", path, {"active": False}, cookie=admin, csrf=token)[0], 200)
            status, _, _ = wsgi_request(self.app, "POST", "/api/bookings",
                                        {"screening_id": screening["id"], "seat_ids": [1], "mode": "reserve"},
                                        cookie=cookie, csrf=csrf)
            self.assertEqual(status, 404)
            wsgi_request(self.app, "PATCH", path, {"active": True}, cookie=admin, csrf=token)

    def test_movie_duration_with_future_screenings_is_locked(self):
        admin, csrf = self.login_admin()
        self.assertEqual(wsgi_request(self.app, "PATCH", "/api/admin/movies/1", {"duration_min": 600}, cookie=admin, csrf=csrf)[0], 409)

    def test_simultaneous_reservations_have_one_winner(self):
        first, first_csrf, _ = self.register()
        second, second_csrf, _ = self.register("second@example.org", "127.0.0.30")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.reserve, cookie, token) for cookie, token in ((first, first_csrf), (second, second_csrf))]
            self.assertEqual(sorted(f.result()[0] for f in futures), [201, 409])

    def test_expired_reservation_releases_seat(self):
        cookie, csrf, _ = self.register()
        _, headers, payload = self.reserve(cookie, csrf)
        from datetime import timedelta
        from cinema.db import utc_now
        with patch("cinema.service.utc_now", return_value=utc_now() + timedelta(minutes=16)):
            status, _, data = wsgi_request(self.app, "GET", headers["location"], cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(data["booking"]["status"], "expired")
        self.assertEqual(self.reserve(cookie, csrf)[0], 201)


if __name__ == "__main__":
    unittest.main()
