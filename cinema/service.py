from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .cache import TTLCache
from .config import Config
from .db import Database, iso_utc, utc_now
from .errors import APIError
from .security import (
    hash_password,
    hash_token,
    new_token,
    normalize_email,
    validate_email,
    validate_password,
    verify_password,
)


MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")


def parse_iso_utc(value: str, field_name: str = "starts_at") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise APIError(422, "validation_error", f"Поле {field_name} обязательно")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise APIError(
            422,
            "validation_error",
            f"Поле {field_name} должно содержать дату и время ISO 8601",
        ) from exc
    if parsed.tzinfo is None:
        raise APIError(
            422,
            "validation_error",
            f"Поле {field_name} должно содержать часовой пояс",
        )
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def require_text(
    payload: dict[str, Any], field: str, *, minimum: int = 1, maximum: int = 5000
) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise APIError(422, "validation_error", f"Поле {field} должно быть строкой")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise APIError(
            422,
            "validation_error",
            f"Длина поля {field} должна быть от {minimum} до {maximum} символов",
        )
    return value


def require_int(
    payload: dict[str, Any], field: str, *, minimum: int, maximum: int
) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise APIError(422, "validation_error", f"Поле {field} должно быть целым числом")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise APIError(
            422, "validation_error", f"Поле {field} должно быть целым числом"
        ) from exc
    if not minimum <= result <= maximum:
        raise APIError(
            422,
            "validation_error",
            f"Поле {field} должно быть от {minimum} до {maximum}",
        )
    return result


class CinemaService:
    def __init__(self, database: Database, config: Config) -> None:
        self.database = database
        self.config = config
        self.public_cache = TTLCache(ttl_seconds=30)

    # Authentication
    def register(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        email = normalize_email(require_text(payload, "email", maximum=254))
        password = payload.get("password")
        if not validate_email(email):
            raise APIError(422, "validation_error", "Укажите корректный адрес электронной почты")
        if not isinstance(password, str):
            raise APIError(422, "validation_error", "Поле password должно быть строкой")
        password_errors = validate_password(password)
        if password_errors:
            raise APIError(422, "weak_password", password_errors[0], password_errors)
        try:
            with self.database.write(immediate=True) as connection:
                cursor = connection.execute(
                    "INSERT INTO users(email, password_hash, role) VALUES (?, ?, 'user')",
                    (email, hash_password(password)),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "email_exists", "Пользователь с таким адресом уже зарегистрирован") from exc
        return self._new_auth_session(user_id)

    def login(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        email = normalize_email(require_text(payload, "email", maximum=254))
        password = payload.get("password")
        if not isinstance(password, str):
            raise APIError(422, "validation_error", "Поле password должно быть строкой")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, email, password_hash, role FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise APIError(401, "invalid_credentials", "Неверный адрес или пароль")
        return self._new_auth_session(int(row["id"]))

    def _new_auth_session(self, user_id: int) -> tuple[dict[str, Any], str, str]:
        raw_token = new_token()
        csrf_token = new_token(24)
        expires_at = utc_now() + timedelta(hours=self.config.session_hours)
        with self.database.write(immediate=True) as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?",
                (iso_utc(utc_now()),),
            )
            connection.execute(
                """
                INSERT INTO auth_sessions(user_id, token_hash, csrf_token, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, hash_token(raw_token), csrf_token, iso_utc(expires_at)),
            )
            row = connection.execute(
                "SELECT id, email, role FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row), raw_token, csrf_token

    def authenticate(self, raw_token: str | None) -> dict[str, Any] | None:
        if not raw_token:
            return None
        token_digest = hash_token(raw_token)
        with self.database.write() as connection:
            row = connection.execute(
                """
                SELECT u.id, u.email, u.role, s.csrf_token, s.expires_at
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_digest,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= iso_utc(utc_now()):
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (token_digest,)
                )
                return None
            return dict(row)

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self.database.write() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (hash_token(raw_token),)
            )

    # Public catalogue
    def list_movies(self) -> list[dict[str, Any]]:
        cached = self.public_cache.get("movies")
        if cached is not None:
            return cached
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, title, description, duration_min, age_rating
                FROM movies
                WHERE active = 1
                ORDER BY title
                """
            ).fetchall()
        result = [dict(row) for row in rows]
        self.public_cache.set("movies", result)
        return result

    def list_screenings(
        self, movie_id: int | None = None, on_date: str | None = None
    ) -> list[dict[str, Any]]:
        date_filter: date | None = None
        if on_date:
            try:
                date_filter = date.fromisoformat(on_date)
            except ValueError as exc:
                raise APIError(
                    422, "validation_error", "Параметр date должен иметь формат YYYY-MM-DD"
                ) from exc
        cache_key = f"screenings:{movie_id or 'all'}:{on_date or 'all'}"
        cached = self.public_cache.get(cache_key)
        if cached is not None:
            return cached
        sql = """
            SELECT s.id, s.movie_id, s.hall_id, s.starts_at, s.description,
                   s.base_price_cents, m.title AS movie_title,
                   m.duration_min, m.age_rating, h.name AS hall_name
            FROM screenings s
            JOIN movies m ON m.id = s.movie_id
            JOIN halls h ON h.id = s.hall_id
            WHERE s.status = 'scheduled' AND m.active = 1 AND h.active = 1
              AND s.starts_at > ?
        """
        params: list[Any] = [iso_utc(utc_now())]
        if movie_id is not None:
            sql += " AND s.movie_id = ?"
            params.append(movie_id)
        sql += " ORDER BY s.starts_at, m.title"
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if date_filter is not None:
                local_date = parse_iso_utc(item["starts_at"]).astimezone(MOSCOW).date()
                if local_date != date_filter:
                    continue
            result.append(item)
        self.public_cache.set(cache_key, result)
        return result

    def screening_seats(self, screening_id: int) -> dict[str, Any]:
        with self.database.write(immediate=True) as connection:
            self._expire_holds(connection)
            screening = connection.execute(
                """
                SELECT s.id, s.hall_id, s.starts_at, s.base_price_cents,
                       m.title AS movie_title, h.name AS hall_name,
                       h.rows_count, h.seats_per_row
                FROM screenings s
                JOIN movies m ON m.id = s.movie_id
                JOIN halls h ON h.id = s.hall_id
                WHERE s.id = ? AND s.status = 'scheduled'
                """,
                (screening_id,),
            ).fetchone()
            if screening is None:
                raise APIError(404, "screening_not_found", "Сеанс не найден")
            seats = connection.execute(
                """
                SELECT seat.id, seat.row_number, seat.seat_number,
                       CASE WHEN t.id IS NULL THEN 1 ELSE 0 END AS available
                FROM seats seat
                LEFT JOIN tickets t
                  ON t.seat_id = seat.id
                 AND t.screening_id = ?
                 AND t.status IN ('reserved', 'paid')
                WHERE seat.hall_id = ?
                ORDER BY seat.row_number, seat.seat_number
                """,
                (screening_id, screening["hall_id"]),
            ).fetchall()
        return {"screening": dict(screening), "seats": [dict(row) for row in seats]}

    # User bookings
    def create_booking(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        screening_id = require_int(payload, "screening_id", minimum=1, maximum=2_000_000_000)
        raw_seat_ids = payload.get("seat_ids")
        if not isinstance(raw_seat_ids, list):
            raise APIError(422, "validation_error", "Поле seat_ids должно быть массивом")
        seat_ids = [
            require_int({"seat_id": item}, "seat_id", minimum=1, maximum=2_000_000_000)
            for item in raw_seat_ids
        ]
        if not 1 <= len(seat_ids) <= 10:
            raise APIError(422, "validation_error", "За один раз можно выбрать от 1 до 10 мест")
        if len(set(seat_ids)) != len(seat_ids):
            raise APIError(422, "validation_error", "Список мест содержит повторы")
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in {"reserve", "purchase"}:
            raise APIError(422, "validation_error", "Поле mode должно быть reserve или purchase")

        now = utc_now()
        booking_status = "reserved" if mode == "reserve" else "paid"
        expires_at = (
            now + timedelta(minutes=self.config.booking_hold_minutes)
            if mode == "reserve"
            else None
        )
        try:
            with self.database.write(immediate=True) as connection:
                self._expire_holds(connection)
                screening = connection.execute(
                    """
                    SELECT s.id, s.hall_id, s.starts_at, s.base_price_cents, s.status
                    FROM screenings s
                    JOIN movies m ON m.id = s.movie_id
                    JOIN halls h ON h.id = s.hall_id
                    WHERE s.id = ? AND m.active = 1 AND h.active = 1
                    """,
                    (screening_id,),
                ).fetchone()
                if screening is None or screening["status"] != "scheduled":
                    raise APIError(404, "screening_not_found", "Сеанс не найден")
                if parse_iso_utc(screening["starts_at"]) <= now:
                    raise APIError(409, "screening_started", "Сеанс уже начался")

                placeholders = ",".join("?" for _ in seat_ids)
                seats = connection.execute(
                    f"""
                    SELECT id FROM seats
                    WHERE hall_id = ? AND id IN ({placeholders})
                    """,
                    [screening["hall_id"], *seat_ids],
                ).fetchall()
                if len(seats) != len(seat_ids):
                    raise APIError(422, "invalid_seats", "Одно или несколько мест не принадлежат залу")

                busy = connection.execute(
                    f"""
                    SELECT seat_id FROM tickets
                    WHERE screening_id = ?
                      AND seat_id IN ({placeholders})
                      AND status IN ('reserved', 'paid')
                    """,
                    [screening_id, *seat_ids],
                ).fetchall()
                if busy:
                    raise APIError(
                        409,
                        "seats_unavailable",
                        "Одно или несколько мест уже заняты",
                        {"seat_ids": [row["seat_id"] for row in busy]},
                    )

                total_cents = int(screening["base_price_cents"]) * len(seat_ids)
                code = f"CIN-{new_token(6).upper()[:8]}"
                now_text = iso_utc(now)
                cursor = connection.execute(
                    """
                    INSERT INTO bookings(
                        user_id, screening_id, code, mode, status, total_cents,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        screening_id,
                        code,
                        mode,
                        booking_status,
                        total_cents,
                        iso_utc(expires_at) if expires_at else None,
                        now_text,
                        now_text,
                    ),
                )
                booking_id = int(cursor.lastrowid)
                connection.executemany(
                    """
                    INSERT INTO tickets(
                        booking_id, screening_id, seat_id, price_cents, status
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            booking_id,
                            screening_id,
                            seat_id,
                            screening["base_price_cents"],
                            booking_status,
                        )
                        for seat_id in seat_ids
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "seats_unavailable", "Выбранные места уже заняты") from exc
        return self.get_booking(user_id, booking_id)

    def list_bookings(self, user_id: int) -> list[dict[str, Any]]:
        with self.database.write(immediate=True) as connection:
            self._expire_holds(connection)
            rows = connection.execute(
                """
                SELECT b.id, b.code, b.mode, b.status, b.total_cents,
                       b.expires_at, b.created_at, s.starts_at,
                       m.title AS movie_title, h.name AS hall_name
                FROM bookings b
                JOIN screenings s ON s.id = b.screening_id
                JOIN movies m ON m.id = s.movie_id
                JOIN halls h ON h.id = s.hall_id
                WHERE b.user_id = ?
                ORDER BY b.created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return self._attach_seats(connection, rows)

    def get_booking(self, user_id: int, booking_id: int) -> dict[str, Any]:
        with self.database.write(immediate=True) as connection:
            self._expire_holds(connection)
            row = connection.execute(
                """
                SELECT b.id, b.code, b.mode, b.status, b.total_cents,
                       b.expires_at, b.created_at, s.starts_at,
                       m.title AS movie_title, h.name AS hall_name
                FROM bookings b
                JOIN screenings s ON s.id = b.screening_id
                JOIN movies m ON m.id = s.movie_id
                JOIN halls h ON h.id = s.hall_id
                WHERE b.id = ? AND b.user_id = ?
                """,
                (booking_id, user_id),
            ).fetchone()
            if row is None:
                raise APIError(404, "booking_not_found", "Бронирование не найдено")
            return self._attach_seats(connection, [row])[0]

    def cancel_booking(
        self, user_id: int, booking_id: int, *, idempotent: bool = False
    ) -> dict[str, Any]:
        with self.database.write(immediate=True) as connection:
            self._expire_holds(connection)
            booking = connection.execute(
                "SELECT id, status FROM bookings WHERE id = ? AND user_id = ?",
                (booking_id, user_id),
            ).fetchone()
            if booking is None:
                raise APIError(404, "booking_not_found", "Бронирование не найдено")
            if idempotent and booking["status"] == "cancelled":
                pass  # Repeated DELETE leaves the same cancelled reservation.
            elif booking["status"] != "reserved":
                raise APIError(
                    409,
                    "booking_not_cancellable",
                    "Отменить можно только действующую бронь",
                )
            if booking["status"] == "reserved":
                now_text = iso_utc(utc_now())
                connection.execute(
                    "UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?",
                    (now_text, booking_id),
                )
                connection.execute(
                    "UPDATE tickets SET status = 'cancelled' WHERE booking_id = ?",
                    (booking_id,),
                )
        return self.get_booking(user_id, booking_id)

    def _attach_seats(
        self, connection: sqlite3.Connection, rows: Iterable[sqlite3.Row]
    ) -> list[dict[str, Any]]:
        items = [dict(row) for row in rows]
        if not items:
            return items
        ids = [item["id"] for item in items]
        placeholders = ",".join("?" for _ in ids)
        ticket_rows = connection.execute(
            f"""
            SELECT t.booking_id, seat.id, seat.row_number, seat.seat_number,
                   t.price_cents, t.status
            FROM tickets t
            JOIN seats seat ON seat.id = t.seat_id
            WHERE t.booking_id IN ({placeholders})
            ORDER BY seat.row_number, seat.seat_number
            """,
            ids,
        ).fetchall()
        by_booking: dict[int, list[dict[str, Any]]] = {item["id"]: [] for item in items}
        for row in ticket_rows:
            ticket = dict(row)
            booking_id = ticket.pop("booking_id")
            by_booking[booking_id].append(ticket)
        for item in items:
            item["seats"] = by_booking[item["id"]]
        return items

    def _expire_holds(self, connection: sqlite3.Connection) -> None:
        now_text = iso_utc(utc_now())
        expired_ids = [
            row["id"]
            for row in connection.execute(
                """
                SELECT id FROM bookings
                WHERE status = 'reserved' AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now_text,),
            ).fetchall()
        ]
        if not expired_ids:
            return
        placeholders = ",".join("?" for _ in expired_ids)
        connection.execute(
            f"UPDATE bookings SET status = 'expired', updated_at = ? WHERE id IN ({placeholders})",
            [now_text, *expired_ids],
        )
        connection.execute(
            f"UPDATE tickets SET status = 'expired' WHERE booking_id IN ({placeholders})",
            expired_ids,
        )

    # Administration
    def list_halls(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, rows_count, seats_per_row, active,
                       rows_count * seats_per_row AS capacity
                FROM halls ORDER BY name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_movie(self, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        title = require_text(payload, "title", maximum=160)
        description = require_text(payload, "description", maximum=3000)
        duration = require_int(payload, "duration_min", minimum=1, maximum=600)
        age_rating = require_text(payload, "age_rating", maximum=10)
        with self.database.write(immediate=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO movies(title, description, duration_min, age_rating)
                VALUES (?, ?, ?, ?)
                """,
                (title, description, duration, age_rating),
            )
            movie_id = int(cursor.lastrowid)
            self._audit(connection, actor_id, "create", "movie", movie_id, payload)
            row = connection.execute(
                """
                SELECT id, title, description, duration_min, age_rating, active
                FROM movies WHERE id = ?
                """,
                (movie_id,),
            ).fetchone()
        self.public_cache.clear()
        return dict(row)

    def update_movie(
        self, actor_id: int, movie_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        assignments: list[str] = []
        values: list[Any] = []
        if "title" in payload:
            assignments.append("title = ?")
            values.append(require_text(payload, "title", maximum=160))
        if "description" in payload:
            assignments.append("description = ?")
            values.append(require_text(payload, "description", maximum=3000))
        if "duration_min" in payload:
            assignments.append("duration_min = ?")
            values.append(require_int(payload, "duration_min", minimum=1, maximum=600))
        if "age_rating" in payload:
            assignments.append("age_rating = ?")
            values.append(require_text(payload, "age_rating", maximum=10))
        if "active" in payload:
            if not isinstance(payload["active"], bool):
                raise APIError(422, "validation_error", "Поле active должно быть логическим")
            assignments.append("active = ?")
            values.append(1 if payload["active"] else 0)
        if not assignments:
            raise APIError(422, "validation_error", "Нет полей для изменения")
        with self.database.write(immediate=True) as connection:
            movie = connection.execute("SELECT duration_min FROM movies WHERE id = ?", (movie_id,)).fetchone()
            if movie is None:
                raise APIError(404, "movie_not_found", "Фильм не найден")
            if "duration_min" in payload and int(payload["duration_min"]) != movie["duration_min"]:
                scheduled = connection.execute(
                    "SELECT 1 FROM screenings WHERE movie_id = ? AND status = 'scheduled' AND starts_at > ? LIMIT 1",
                    (movie_id, iso_utc(utc_now())),
                ).fetchone()
                if scheduled:
                    raise APIError(409, "movie_locked", "Длительность фильма с будущими сеансами менять нельзя")
            values.append(movie_id)
            cursor = connection.execute(
                f"UPDATE movies SET {', '.join(assignments)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise APIError(404, "movie_not_found", "Фильм не найден")
            self._audit(connection, actor_id, "update", "movie", movie_id, payload)
            row = connection.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
        self.public_cache.clear()
        return dict(row)

    def create_hall(self, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        name = require_text(payload, "name", maximum=100)
        rows_count = require_int(payload, "rows_count", minimum=1, maximum=30)
        seats_per_row = require_int(payload, "seats_per_row", minimum=1, maximum=50)
        try:
            with self.database.write(immediate=True) as connection:
                cursor = connection.execute(
                    "INSERT INTO halls(name, rows_count, seats_per_row) VALUES (?, ?, ?)",
                    (name, rows_count, seats_per_row),
                )
                hall_id = int(cursor.lastrowid)
                self._insert_seats(connection, hall_id, rows_count, seats_per_row)
                self._audit(connection, actor_id, "create", "hall", hall_id, payload)
        except sqlite3.IntegrityError as exc:
            raise APIError(409, "hall_exists", "Зал с таким названием уже существует") from exc
        self.public_cache.clear()
        return next(item for item in self.list_halls() if item["id"] == hall_id)

    def update_hall(
        self, actor_id: int, hall_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.database.write(immediate=True) as connection:
            hall = connection.execute("SELECT * FROM halls WHERE id = ?", (hall_id,)).fetchone()
            if hall is None:
                raise APIError(404, "hall_not_found", "Зал не найден")
            name = hall["name"]
            active = int(hall["active"])
            rows_count = int(hall["rows_count"])
            seats_per_row = int(hall["seats_per_row"])
            if "name" in payload:
                name = require_text(payload, "name", maximum=100)
            if "active" in payload:
                if not isinstance(payload["active"], bool):
                    raise APIError(422, "validation_error", "Поле active должно быть логическим")
                active = 1 if payload["active"] else 0
            new_rows = (
                require_int(payload, "rows_count", minimum=1, maximum=30)
                if "rows_count" in payload
                else rows_count
            )
            new_seats = (
                require_int(payload, "seats_per_row", minimum=1, maximum=50)
                if "seats_per_row" in payload
                else seats_per_row
            )
            layout_changed = new_rows != rows_count or new_seats != seats_per_row
            if layout_changed:
                ticket_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM tickets t
                    JOIN seats s ON s.id = t.seat_id
                    WHERE s.hall_id = ?
                    """,
                    (hall_id,),
                ).fetchone()[0]
                future_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM screenings
                    WHERE hall_id = ? AND status = 'scheduled' AND starts_at > ?
                    """,
                    (hall_id, iso_utc(utc_now())),
                ).fetchone()[0]
                if ticket_count or future_count:
                    raise APIError(
                        409,
                        "hall_layout_locked",
                        "Схему зала нельзя менять при наличии билетов или будущих сеансов",
                    )
                connection.execute("DELETE FROM seats WHERE hall_id = ?", (hall_id,))
                self._insert_seats(connection, hall_id, new_rows, new_seats)
            try:
                connection.execute(
                    """
                    UPDATE halls
                    SET name = ?, active = ?, rows_count = ?, seats_per_row = ?
                    WHERE id = ?
                    """,
                    (name, active, new_rows, new_seats, hall_id),
                )
            except sqlite3.IntegrityError as exc:
                raise APIError(409, "hall_exists", "Зал с таким названием уже существует") from exc
            self._audit(connection, actor_id, "update", "hall", hall_id, payload)
        self.public_cache.clear()
        return next(item for item in self.list_halls() if item["id"] == hall_id)

    def _insert_seats(
        self, connection: sqlite3.Connection, hall_id: int, rows_count: int, seats_per_row: int
    ) -> None:
        connection.executemany(
            "INSERT INTO seats(hall_id, row_number, seat_number) VALUES (?, ?, ?)",
            (
                (hall_id, row_number, seat_number)
                for row_number in range(1, rows_count + 1)
                for seat_number in range(1, seats_per_row + 1)
            ),
        )

    def create_screening(
        self, actor_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        movie_id = require_int(payload, "movie_id", minimum=1, maximum=2_000_000_000)
        hall_id = require_int(payload, "hall_id", minimum=1, maximum=2_000_000_000)
        starts_at = parse_iso_utc(str(payload.get("starts_at", "")))
        if starts_at <= utc_now() + timedelta(minutes=10):
            raise APIError(422, "validation_error", "Сеанс должен начинаться минимум через 10 минут")
        price = require_int(payload, "base_price_cents", minimum=0, maximum=10_000_000)
        description_value = payload.get("description", "")
        if not isinstance(description_value, str) or len(description_value.strip()) > 1000:
            raise APIError(422, "validation_error", "Некорректное описание сеанса")
        description = description_value.strip()
        with self.database.write(immediate=True) as connection:
            movie = connection.execute(
                "SELECT id, duration_min, active FROM movies WHERE id = ?", (movie_id,)
            ).fetchone()
            hall = connection.execute(
                "SELECT id, active FROM halls WHERE id = ?", (hall_id,)
            ).fetchone()
            if movie is None or not movie["active"]:
                raise APIError(422, "invalid_movie", "Фильм не найден или снят с показа")
            if hall is None or not hall["active"]:
                raise APIError(422, "invalid_hall", "Зал не найден или отключён")
            self._assert_no_overlap(
                connection, hall_id, starts_at, int(movie["duration_min"])
            )
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO screenings(
                        movie_id, hall_id, starts_at, description, base_price_cents
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (movie_id, hall_id, iso_utc(starts_at), description, price),
                )
            except sqlite3.IntegrityError as exc:
                raise APIError(409, "screening_conflict", "Зал уже занят в это время") from exc
            screening_id = int(cursor.lastrowid)
            self._audit(connection, actor_id, "create", "screening", screening_id, payload)
        self.public_cache.clear()
        return self._get_screening_admin(screening_id)

    def update_screening_price(
        self, actor_id: int, screening_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        price = require_int(payload, "base_price_cents", minimum=0, maximum=10_000_000)
        with self.database.write(immediate=True) as connection:
            screening = connection.execute(
                "SELECT id, starts_at, status FROM screenings WHERE id = ?", (screening_id,)
            ).fetchone()
            if screening is None:
                raise APIError(404, "screening_not_found", "Сеанс не найден")
            if screening["status"] != "scheduled" or parse_iso_utc(screening["starts_at"]) <= utc_now():
                raise APIError(409, "screening_locked", "Цену завершённого или отменённого сеанса менять нельзя")
            connection.execute(
                "UPDATE screenings SET base_price_cents = ? WHERE id = ?",
                (price, screening_id),
            )
            self._audit(
                connection,
                actor_id,
                "update_price",
                "screening",
                screening_id,
                {"base_price_cents": price},
            )
        self.public_cache.clear()
        return self._get_screening_admin(screening_id)

    def _assert_no_overlap(
        self,
        connection: sqlite3.Connection,
        hall_id: int,
        starts_at: datetime,
        duration_min: int,
    ) -> None:
        cleanup = timedelta(minutes=15)
        proposed_end = starts_at + timedelta(minutes=duration_min)
        existing = connection.execute(
            """
            SELECT s.id, s.starts_at, m.duration_min
            FROM screenings s
            JOIN movies m ON m.id = s.movie_id
            WHERE s.hall_id = ? AND s.status = 'scheduled'
            """,
            (hall_id,),
        ).fetchall()
        for row in existing:
            existing_start = parse_iso_utc(row["starts_at"])
            existing_end = existing_start + timedelta(minutes=row["duration_min"])
            if starts_at < existing_end + cleanup and existing_start < proposed_end + cleanup:
                raise APIError(
                    409,
                    "screening_conflict",
                    "Сеанс пересекается с другим показом с учётом уборки зала",
                    {"conflicting_screening_id": row["id"]},
                )

    def _get_screening_admin(self, screening_id: int) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT s.id, s.movie_id, s.hall_id, s.starts_at, s.description,
                       s.base_price_cents, s.status, m.title AS movie_title,
                       h.name AS hall_name
                FROM screenings s
                JOIN movies m ON m.id = s.movie_id
                JOIN halls h ON h.id = s.hall_id
                WHERE s.id = ?
                """,
                (screening_id,),
            ).fetchone()
        if row is None:
            raise APIError(404, "screening_not_found", "Сеанс не найден")
        return dict(row)

    def _audit(
        self,
        connection: sqlite3.Connection,
        actor_id: int,
        action: str,
        entity_type: str,
        entity_id: int,
        details: dict[str, Any],
    ) -> None:
        safe_details = {
            key: value for key, value in details.items() if "password" not in key.lower()
        }
        connection.execute(
            """
            INSERT INTO audit_log(
                actor_user_id, action, entity_type, entity_id, details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                action,
                entity_type,
                entity_id,
                json.dumps(safe_details, ensure_ascii=False, separators=(",", ":")),
            ),
        )
