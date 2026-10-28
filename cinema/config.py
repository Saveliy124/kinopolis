from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root_dir: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    session_hours: int = 8
    booking_hold_minutes: int = 15
    cookie_secure: bool = False
    admin_email: str = "admin@cinema.local"
    admin_password: str = "Admin123!"

    @classmethod
    def from_env(cls, root_dir: Path | None = None) -> "Config":
        root = (root_dir or Path(__file__).resolve().parent.parent).resolve()
        database = Path(os.getenv("CINEMA_DB", str(root / "data" / "cinema.db")))
        if not database.is_absolute():
            database = (root / database).resolve()
        return cls(
            root_dir=root,
            database_path=database,
            host=os.getenv("CINEMA_HOST", "127.0.0.1"),
            port=int(os.getenv("CINEMA_PORT", "8000")),
            session_hours=int(os.getenv("SESSION_HOURS", "8")),
            booking_hold_minutes=int(os.getenv("BOOKING_HOLD_MINUTES", "15")),
            cookie_secure=os.getenv("COOKIE_SECURE", "0") == "1",
            admin_email=os.getenv("ADMIN_EMAIL", "admin@cinema.local"),
            admin_password=os.getenv("ADMIN_PASSWORD", "Admin123!"),
        )
