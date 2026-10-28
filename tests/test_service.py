from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from cinema.config import Config
from cinema.db import iso_utc, utc_now
from cinema.errors import APIError
from cinema.web import create_application


ROOT = Path(__file__).resolve().parent.parent


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config = Config(
            root_dir=ROOT,
            database_path=Path(self.temp_dir.name) / "test.db",
            admin_password="AdminPass1",
            booking_hold_minutes=15,
        )
        self.app = create_application(config)
        self.service = self.app.service

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, email: str):
        user, _, _ = self.service.register({"email": email, "password": "UserPass1"})
        return user

    def test_seed_data_and_seat_count(self):
        movies = self.service.list_movies()
        screenings = self.service.list_screenings()
        self.assertEqual(len(movies), 3)
        self.assertGreaterEqual(len(screenings), 4)
        seat_data = self.service.screening_seats(screenings[0]["id"])
        expected = seat_data["screening"]["rows_count"] * seat_data["screening"]["seats_per_row"]
        self.assertEqual(len(seat_data["seats"]), expected)

    def test_atomic_conflict_then_cancel_releases_seat(self):
        first_user = self.register("first@example.org")
        second_user = self.register("second@example.org")
        screening = self.service.list_screenings()[0]
        seat_id = self.service.screening_seats(screening["id"])["seats"][0]["id"]

        first_booking = self.service.create_booking(
            first_user["id"],
            {"screening_id": screening["id"], "seat_ids": [seat_id], "mode": "reserve"},
        )
        self.assertEqual(first_booking["status"], "reserved")
        with self.assertRaises(APIError) as conflict:
            self.service.create_booking(
                second_user["id"],
                {"screening_id": screening["id"], "seat_ids": [seat_id], "mode": "purchase"},
            )
        self.assertEqual(conflict.exception.code, "seats_unavailable")

        cancelled = self.service.cancel_booking(first_user["id"], first_booking["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        second_booking = self.service.create_booking(
            second_user["id"],
            {"screening_id": screening["id"], "seat_ids": [seat_id], "mode": "purchase"},
        )
        self.assertEqual(second_booking["status"], "paid")

    def test_paid_booking_cannot_be_cancelled(self):
        user = self.register("buyer@example.org")
        screening = self.service.list_screenings()[0]
        seat_id = self.service.screening_seats(screening["id"])["seats"][0]["id"]
        booking = self.service.create_booking(
            user["id"],
            {"screening_id": screening["id"], "seat_ids": [seat_id], "mode": "purchase"},
        )
        with self.assertRaises(APIError) as error:
            self.service.cancel_booking(user["id"], booking["id"])
        self.assertEqual(error.exception.code, "booking_not_cancellable")

    def test_overlapping_screening_is_rejected(self):
        admin, _, _ = self.service.login(
            {"email": "admin@cinema.local", "password": "AdminPass1"}
        )
        movie = self.service.list_movies()[0]
        hall = self.service.list_halls()[0]
        start = utc_now() + timedelta(days=8)
        first = self.service.create_screening(
            admin["id"],
            {
                "movie_id": movie["id"],
                "hall_id": hall["id"],
                "starts_at": iso_utc(start),
                "description": "Первый",
                "base_price_cents": 50000,
            },
        )
        self.assertEqual(first["status"], "scheduled")
        with self.assertRaises(APIError) as error:
            self.service.create_screening(
                admin["id"],
                {
                    "movie_id": movie["id"],
                    "hall_id": hall["id"],
                    "starts_at": iso_utc(start + timedelta(minutes=30)),
                    "description": "Пересечение",
                    "base_price_cents": 50000,
                },
            )
        self.assertEqual(error.exception.code, "screening_conflict")


if __name__ == "__main__":
    unittest.main()
