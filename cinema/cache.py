from __future__ import annotations

import copy
import threading
import time
from typing import Any


class TTLCache:
    """Небольшой потокобезопасный кэш для публичного каталога."""

    def __init__(self, ttl_seconds: int = 30) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._values.pop(key, None)
                return None
            return copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = (
                time.monotonic() + self.ttl_seconds,
                copy.deepcopy(value),
            )

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
