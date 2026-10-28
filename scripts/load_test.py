#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def request_once(url: str, timeout: float) -> tuple[float, int]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            status = response.status
    except Exception:
        status = 0
    return (time.perf_counter() - started) * 1000, status


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Нагрузочная проверка HTTP-эндпоинта")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/movies")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    started = time.perf_counter()
    results: list[tuple[float, int]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(request_once, args.url, args.timeout)
            for _ in range(args.requests)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started
    durations = [duration for duration, _ in results]
    ok = sum(1 for _, status in results if 200 <= status < 400)
    print(f"Запросов: {len(results)}; успешно: {ok}; ошибок: {len(results) - ok}")
    print(f"Время: {elapsed:.2f} с; пропускная способность: {len(results) / elapsed:.1f} запр./с")
    print(
        f"Задержка: средняя {statistics.mean(durations):.1f} мс; "
        f"p50 {percentile(durations, 0.50):.1f} мс; "
        f"p95 {percentile(durations, 0.95):.1f} мс"
    )


if __name__ == "__main__":
    main()
