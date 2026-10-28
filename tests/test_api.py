from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cinema.config import Config
from cinema.web import create_application
from tests.helpers import cookie_from_headers, wsgi_request


ROOT = Path(__file__).resolve().parent.parent


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = Config(
            root_dir=ROOT,
            database_path=Path(self.temp_dir.name) / "api.db",
            admin_password="AdminPass1",
        )
        self.app = create_application(config)

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, email="web@example.org", remote="127.0.0.10"):
        status, headers, payload = wsgi_request(
            self.app,
            "POST",
            "/api/auth/register",
            {"email": email, "password": "UserPass1"},
            remote_addr=remote,
        )
        self.assertEqual(status, 201)
        return cookie_from_headers(headers), payload["csrf_token"], payload["user"]

    def test_health_and_static_page(self):
        status, _, payload = wsgi_request(self.app, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        status, headers, html = wsgi_request(self.app, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn("Кинополис", html)

    def test_health_rejects_missing_database_without_recreating_it(self):
        database_path = self.app.config.database_path
        database_path.unlink()

        status, _, payload = wsgi_request(self.app, "GET", "/api/health")

        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "unhealthy")
        self.assertFalse(database_path.exists())

    def test_csrf_is_required_for_booking(self):
        cookie, _, _ = self.register()
        _, _, screenings = wsgi_request(self.app, "GET", "/api/screenings")
        screening_id = screenings["screenings"][0]["id"]
        _, _, seats = wsgi_request(
            self.app, "GET", f"/api/screenings/{screening_id}/seats"
        )
        seat_id = seats["seats"][0]["id"]
        status, _, payload = wsgi_request(
            self.app,
            "POST",
            "/api/bookings",
            {"screening_id": screening_id, "seat_ids": [seat_id], "mode": "reserve"},
            cookie=cookie,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "csrf_failed")

    def test_reserve_list_and_cancel_through_api(self):
        cookie, csrf, _ = self.register("flow@example.org", "127.0.0.11")
        _, _, screenings = wsgi_request(self.app, "GET", "/api/screenings")
        screening_id = screenings["screenings"][0]["id"]
        _, _, seat_data = wsgi_request(
            self.app, "GET", f"/api/screenings/{screening_id}/seats"
        )
        seat_ids = [item["id"] for item in seat_data["seats"][:2]]
        status, _, payload = wsgi_request(
            self.app,
            "POST",
            "/api/bookings",
            {"screening_id": screening_id, "seat_ids": seat_ids, "mode": "reserve"},
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 201)
        booking = payload["booking"]
        self.assertEqual(booking["status"], "reserved")
        self.assertEqual(len(booking["seats"]), 2)

        status, _, payload = wsgi_request(
            self.app,
            "POST",
            f"/api/bookings/{booking['id']}/cancel",
            {},
            cookie=cookie,
            csrf=csrf,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["booking"]["status"], "cancelled")

    def test_regular_user_cannot_access_admin(self):
        cookie, _, _ = self.register("plain@example.org", "127.0.0.12")
        status, _, payload = wsgi_request(
            self.app, "GET", "/api/admin/halls", cookie=cookie
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "admin_required")


if __name__ == "__main__":
    unittest.main()
