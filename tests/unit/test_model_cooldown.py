from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lecturelog.infrastructure.llm.model_cooldown import (
    ModelCooldown,
    seconds_until_pacific_midnight,
)

_PACIFIC = ZoneInfo("America/Los_Angeles")


class FakeClock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.mark.asyncio
async def test_acquire_returns_first_model():
    cd = ModelCooldown(time_func=FakeClock())
    assert await cd.acquire(["A", "B"]) == "A"


@pytest.mark.asyncio
async def test_cooldown_skips_to_next_model():
    clock = FakeClock()
    cd = ModelCooldown(time_func=clock)
    await cd.mark_rate_limited("A", ttl=30.0)
    assert await cd.acquire(["A", "B"]) == "B"  # A остывает
    clock.advance(31)
    assert await cd.acquire(["A", "B"]) == "A"  # остыла


@pytest.mark.asyncio
async def test_all_cooling_returns_least_cooling():
    # когда все модели остывают — вернуть ту, что освободится раньше (не падать)
    clock = FakeClock()
    cd = ModelCooldown(time_func=clock)
    await cd.mark_rate_limited("A", ttl=10.0)
    await cd.mark_rate_limited("B", ttl=50.0)
    assert await cd.acquire(["A", "B"]) == "A"


@pytest.mark.asyncio
async def test_acquire_empty_models_raises_value_error():
    cd = ModelCooldown(time_func=FakeClock())
    with pytest.raises(ValueError):
        await cd.acquire([])


def test_seconds_until_pacific_midnight_spring_dst():
    # 2026-03-08 00:30 PT — переход на летнее время (DST spring-forward).
    epoch = datetime(2026, 3, 8, 0, 30, tzinfo=_PACIFIC).timestamp()
    assert seconds_until_pacific_midnight(epoch) == 81000.0


def test_seconds_until_pacific_midnight_fall_dst():
    # 2026-11-01 00:30 PT — переход с летнего времени (DST fall-back).
    epoch = datetime(2026, 11, 1, 0, 30, tzinfo=_PACIFIC).timestamp()
    assert seconds_until_pacific_midnight(epoch) == 88200.0
