import asyncio
from dataclasses import dataclass

from lecturelog.llm.key_pool import KeyPool


@dataclass
class DummyClient:
    name: str


def test_key_pool_round_robin_acquire():
    pool = KeyPool([DummyClient("a"), DummyClient("b")], rpm_per_key=10_000)

    async def _run():
        first, first_idx = await pool.acquire()
        second, second_idx = await pool.acquire()
        return first.name, first_idx, second.name, second_idx

    first_name, first_idx, second_name, second_idx = asyncio.run(_run())

    assert (first_name, first_idx) == ("a", 0)
    assert (second_name, second_idx) == ("b", 1)


def test_mark_rate_limited_excludes_key_from_alive_count():
    pool = KeyPool([DummyClient("a"), DummyClient("b")], rpm_per_key=10_000)
    pool.mark_rate_limited(0)
    assert pool.alive_count() == 1


def test_acquire_skips_rate_limited_key():
    pool = KeyPool([DummyClient("a"), DummyClient("b")], rpm_per_key=10_000)
    pool.mark_rate_limited(0)

    async def _run():
        client, idx = await pool.acquire()
        return client.name, idx

    assert asyncio.run(_run()) == ("b", 1)
