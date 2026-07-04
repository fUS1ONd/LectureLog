from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_PACIFIC = ZoneInfo("America/Los_Angeles")


def seconds_until_pacific_midnight(epoch: float) -> float:
    """Секунды до ближайшей полуночи по Pacific.

    Копия хелпера из key_pool.py (тот модуль будет удалён позже — логика
    полуночи уже проверена, поэтому копируем дословно, а не импортируем).
    """
    now = datetime.fromtimestamp(epoch, tz=_PACIFIC)
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_PACIFIC)
    # Вычитание datetime с одинаковым tzinfo даёт наивную "wall-clock" разницу
    # и игнорирует смену UTC-смещения в день перехода DST (ошибка ~1ч).
    # Считаем через абсолютное время (unix timestamp), чтобы DST учитывался верно.
    return midnight.timestamp() - now.timestamp()


class ModelCooldown:
    """Process-wide реактивный cooldown моделей при 429.

    acquire(models) — первая не-остывающая модель списка (порядок = приоритет).
    Если все остывают — та, что освободится раньше (fallback, не падаем).
    """

    def __init__(self, time_func: Callable[[], float] | None = None) -> None:
        self._time = time_func or time.time
        self._blocked_until: dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def acquire(self, models: list[str]) -> str:
        if not models:
            # Пустой список моделей — ошибка вызывающего кода, а не повод
            # падать необработанным IndexError глубоко внутри.
            raise ValueError("models must not be empty")
        async with self._lock:
            now = self._time()
            soonest, soonest_at = models[0], float("inf")
            for m in models:
                if now >= self._blocked_until[m]:
                    return m
                if self._blocked_until[m] < soonest_at:
                    soonest, soonest_at = m, self._blocked_until[m]
            logger.warning("все модели остывают, беру %s (раньше всех освободится)", soonest)
            return soonest

    async def mark_rate_limited(self, model: str, ttl: float) -> None:
        async with self._lock:
            self._blocked_until[model] = self._time() + ttl

    def seconds_to_midnight(self) -> float:
        return seconds_until_pacific_midnight(self._time())
