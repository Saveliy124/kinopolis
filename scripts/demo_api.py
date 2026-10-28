"""Run a real HTTP REST demonstration against a local teaching database."""
from __future__ import annotations

import argparse
import json
import os
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf = ""

    def request(self, method, path, payload=None, expected=200):
        headers = {"Accept": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read()
                data = json.loads(raw) if raw else {}
                if response.status != expected:
                    raise RuntimeError(f"Expected {expected}, got {response.status}")
                self.csrf = data.get("csrf_token", self.csrf)
                print(f"{method:6} {path} -> {response.status}")
                return data
        except HTTPError as exc:
            raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode('utf-8')}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Demonstrate GET, POST, PUT, PATCH and DELETE")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    admin = Client(args.url)
    admin.request("POST", "/api/auth/login", {
        "email": os.getenv("ADMIN_EMAIL", "admin@cinema.local"),
        "password": os.getenv("ADMIN_PASSWORD", "Admin123!"),
    })
    movie = {"title": "REST demo", "description": "Practical 11-12", "duration_min": 90, "age_rating": "12+"}
    created = admin.request("POST", "/api/admin/movies", movie, 201)["movie"]
    ident = created["id"]
    admin.request("GET", f"/api/movies/{ident}")
    admin.request("PUT", f"/api/admin/movies/{ident}", {**movie, "title": "REST demo updated", "active": True})
    admin.request("PATCH", f"/api/admin/movies/{ident}", {"active": False})

    user = Client(args.url)
    user.request("POST", "/api/auth/register", {
        "email": f"demo-{uuid4().hex[:10]}@example.org", "password": "DemoPass123!",
    }, 201)
    screenings = user.request("GET", "/api/screenings")["screenings"]
    if not screenings:
        raise RuntimeError("Create a future screening in the admin panel first")
    screening_id = screenings[0]["id"]
    seats = user.request("GET", f"/api/screenings/{screening_id}/seats")["seats"]
    seat = next((seat for seat in seats if seat["available"]), None)
    if seat is None:
        raise RuntimeError("The first screening has no free seats")
    booking = user.request("POST", "/api/bookings", {
        "screening_id": screening_id, "seat_ids": [seat["id"]], "mode": "reserve",
    }, 201)["booking"]
    path = f"/api/bookings/{booking['id']}"
    user.request("GET", path)
    user.request("DELETE", path, expected=204)
    user.request("DELETE", path, expected=204)
    if user.request("GET", path)["booking"]["status"] != "cancelled":
        raise RuntimeError("Reservation was not cancelled")
    print("OK: REST demonstration completed; reservation released")


if __name__ == "__main__":
    main()
