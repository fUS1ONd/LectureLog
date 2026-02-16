from __future__ import annotations

import asyncio
import time
from typing import Any


class KeyPool:
    def __init__(self, clients: list[Any], rpm_per_key: int = 12, model: str = "gemini-2.5-pro"):
        self._clients = clients
        self._lock = asyncio.Lock()
        self._last_request: list[float] = [0.0] * len(clients)
        self._blocked_until: list[float] = [0.0] * len(clients)
        self._next_idx = 0
        self._interval = 60.0 / rpm_per_key if rpm_per_key > 0 else 0.0
        self.model = model

    async def acquire(self) -> tuple[Any, int]:
        if not self._clients:
            raise RuntimeError("GEMINI clients are not configured")

        while True:
            async with self._lock:
                now = time.time()
                for _ in range(len(self._clients)):
                    idx = self._next_idx
                    self._next_idx = (self._next_idx + 1) % len(self._clients)

                    if now < self._blocked_until[idx]:
                        continue

                    wait = self._last_request[idx] + self._interval - now
                    if wait <= 0:
                        self._last_request[idx] = now
                        return self._clients[idx], idx

                min_wait = float("inf")
                for i in range(len(self._clients)):
                    unblock = max(self._blocked_until[i], self._last_request[i] + self._interval)
                    min_wait = min(min_wait, unblock - now)

            await asyncio.sleep(max(0.05, min_wait))

    def mark_rate_limited(self, idx: int):
        if idx < 0 or idx >= len(self._blocked_until):
            return
        self._blocked_until[idx] = time.time() + 60.0

    def alive_count(self) -> int:
        now = time.time()
        return sum(1 for blocked_until in self._blocked_until if blocked_until <= now)
