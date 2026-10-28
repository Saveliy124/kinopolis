from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .config import Config
from .security import hash_password, normalize_email


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.database_path
        self.schema_path = config.root_dir / "database" / "schema.sql"

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def is_healthy(self) -> bool:
        """Check the existing database and schema without creating a new file."""
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
        except sqlite3.Error:
            return False
        try:
            connection.execute("SELECT 1 FROM movies LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False
        finally:
            connection.close()

    @contextmanager
    def write(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def initialize(self, seed: bool = True) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.connection() as connection:
            connection.executescript(schema)
            connection.commit()
        if seed:
            self._seed()

    def _seed(self) -> None:
        with self.write(immediate=True) as connection:
            admin_email = normalize_email(self.config.admin_email)
            existing_admin = connection.execute(
                "SELECT id FROM users WHERE email = ?", (admin_email,)
            ).fetchone()
            if existing_admin is None:
                connection.execute(
                    "INSERT INTO users(email, password_hash, role) VALUES (?, ?, 'admin')",
                    (admin_email, hash_password(self.config.admin_password)),
                )

            if connection.execute("SELECT COUNT(*) FROM halls").fetchone()[0] == 0:
                for name, rows_count, seats_per_row in (
                    ("Большой зал", 5, 8),
                    ("Малый зал", 4, 6),
                ):
                    cursor = connection.execute(
                        "INSERT INTO halls(name, rows_count, seats_per_row) VALUES (?, ?, ?)",
                        (name, rows_count, seats_per_row),
                    )
                    hall_id = cursor.lastrowid
                    connection.executemany(
                        "INSERT INTO seats(hall_id, row_number, seat_number) VALUES (?, ?, ?)",
                        (
                            (hall_id, row_number, seat_number)
                            for row_number in range(1, rows_count + 1)
                            for seat_number in range(1, seats_per_row + 1)
                        ),
                    )

            if connection.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
                connection.executemany(
                    """
                    INSERT INTO movies(title, description, duration_min, age_rating)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            "Предел орбиты",
                            "Научно-фантастическая драма о спасательной экспедиции у далёкой станции.",
                            128,
                            "12+",
                        ),
                        (
                            "Ночной экспресс",
                            "Камерный детектив о пассажирах последнего поезда и пропавшем багаже.",
                            104,
                            "16+",
                        ),
                        (
                            "Город света",
                            "Семейная история о команде подростков, которая возвращает свет своему району.",
                            96,
                            "6+",
                        ),
                    ),
                )

            if connection.execute("SELECT COUNT(*) FROM screenings").fetchone()[0] == 0:
                movies = connection.execute(
                    "SELECT id, title FROM movies ORDER BY id"
                ).fetchall()
                halls = connection.execute("SELECT id FROM halls ORDER BY id").fetchall()
                moscow = timezone(timedelta(hours=3), name="Europe/Moscow")
                local_now = datetime.now(moscow)
                first_day = (local_now + timedelta(days=1)).date()
                schedule = (
                    (0, 0, 18, 0, 55000, "Премьера с коротким вступлением перед показом"),
                    (1, 1, 20, 30, 42000, "Вечерний сеанс"),
                    (2, 0, 14, 0, 35000, "Семейный сеанс"),
                    (0, 1, 19, 0, 48000, "Показ в малом зале"),
                )
                for offset, (movie_idx, hall_idx, hour, minute, price, description) in enumerate(schedule):
                    day = first_day + timedelta(days=offset // 2)
                    starts_local = datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=moscow
                    )
                    connection.execute(
                        """
                        INSERT INTO screenings(
                            movie_id, hall_id, starts_at, description, base_price_cents
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            movies[movie_idx]["id"],
                            halls[hall_idx]["id"],
                            iso_utc(starts_local),
                            description,
                            price,
                        ),
                    )
