from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class KeyPool:
    """Пул клиентов с round-robin и защитой от rate-limit."""

    def __init__(
        self,
        clients: list[Any],
        rpm_per_key: int = 12,
        block_seconds: float = 60.0,
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not clients:
            raise ValueError("Список клиентов не должен быть пустым")
        if rpm_per_key <= 0:
            raise ValueError("rpm_per_key должен быть больше нуля")

        self._clients = clients
        self._lock = asyncio.Lock()
        self._last_request: list[float] = [0.0] * len(clients)
        self._blocked_until: list[float] = [0.0] * len(clients)
        self._next_idx = 0
        self._interval = 60.0 / rpm_per_key
        self._block_seconds = block_seconds
        self._time = time_func or time.time
        self._sleep = sleep_func or asyncio.sleep

    async def acquire(self) -> tuple[Any, int]:
        while True:
            min_wait = float("inf")
            now = self._time()

            async with self._lock:
                for _ in range(len(self._clients)):
                    idx = self._next_idx
                    self._next_idx = (self._next_idx + 1) % len(self._clients)

                    if now < self._blocked_until[idx]:
                        min_wait = min(min_wait, self._blocked_until[idx] - now)
                        continue

                    wait = self._last_request[idx] + self._interval - now
                    if wait <= 0:
                        self._last_request[idx] = now
                        return self._clients[idx], idx
                    min_wait = min(min_wait, wait)

            await self._sleep(max(0.05, min_wait))

    def mark_rate_limited(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._clients):
            raise IndexError("Некорректный индекс ключа")
        self._blocked_until[idx] = self._time() + self._block_seconds

    def alive_count(self) -> int:
        now = self._time()
        return sum(1 for blocked_until in self._blocked_until if blocked_until <= now)

